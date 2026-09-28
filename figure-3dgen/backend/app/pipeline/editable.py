"""Stage 4 — Editable: rebuild every semantic part in Blender as a NEW closed solid, then make it
agree with the approved photo views (visual-hull correction loop). The AI GLB surface is never adopted.

Loop:  Blender rebuild -> silhouette vs photo views -> (excess) carve with visual hull -> Blender rebuild
       -> ... (max 2 corrections) -> gates. Deficits (photo shape missing in the model) are reported,
       never invented.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import trimesh

from .. import jobs
from ..blender.runner import run_plan
from ..config import criteria, settings
from ..geometry import meshio, visual_hull
from ..geometry.audit import audit
from ..geometry.render import compare, diff_image, model_mask, png_bytes
from .split import load_split
from .views import load_masks

MASK_SIZE = 384


def _bounds(parts):
    return np.array([np.min([m.bounds[0] for m in parts.values()], 0), np.max([m.bounds[1] for m in parts.values()], 0)])


def rebuild(job_id: str, parts: dict[str, trimesh.Trimesh], work: Path, *, voxel: float, mode: str, smooth: int,
            label: str) -> tuple[dict[str, trimesh.Trimesh], dict[str, dict]]:
    """One disposable Blender process per part (a slow part cannot sink the others)."""
    work.mkdir(parents=True, exist_ok=True)
    thresholds = criteria()["editable"]["thresholds"]
    total_area = sum(m.area for m in parts.values())
    done = [0]

    def one(name):
        folder = work / name
        folder.mkdir(exist_ok=True)
        m = parts[name]
        np.savez_compressed(folder / "in.npz", vertices=np.asarray(m.vertices), faces=np.asarray(m.faces))
        share = m.area / max(total_area, 1e-9)
        target = int(np.clip(settings.target_quads * np.sqrt(share) * 1.6, 400, settings.target_quads))
        ops = [dict(op="load", name=name, file="in.npz"),
               dict(op="rebuild", part=name, mode=mode, voxel_mm=voxel, shell_mm=thresholds["designed_shell_thickness_mm"],
                    target_quads=target, smooth_iterations=smooth),
               dict(op="save_npz", part=name, file="out.npz"),
               dict(op="export", blend="part.blend", obj="part.obj")]
        result = run_plan(folder, ops, budget_s=settings.part_seconds)
        with np.load(folder / "out.npz") as raw:
            mesh = trimesh.Trimesh(raw["vertices"], raw["faces"], process=False)
        done[0] += 1
        jobs.progress(job_id, "editable", 5 + 40 * done[0] / len(parts), f"{label}: Blender 재구성 {done[0]}/{len(parts)} · {name}")
        return name, mesh, next(o for o in result["ops"] if o["op"] == "rebuild")

    out, recs = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, min(3, (os.cpu_count() or 2) - 1))) as pool:
        for name, mesh, rec in pool.map(one, list(parts)):
            out[name], recs[name] = mesh, rec
    return {n: out[n] for n in parts}, recs


def silhouettes(parts, masks, out: Path, tag: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    bounds = _bounds(parts)
    rows = {}
    for view, photo in masks.items():
        model = model_mask(parts, view, MASK_SIZE, bounds)
        rows[view] = compare(model, photo)
        (out / f"{tag}-{view}.png").write_bytes(png_bytes(diff_image(model, photo)))
    return rows


def build(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    job = jobs.load(job_id)
    jobs.require(job, "split", "approved")
    crit = criteria()["editable"]
    th = crit["thresholds"]
    out = root / "editable"
    out.mkdir(exist_ok=True)
    src = load_split(root)
    masks = load_masks(root)
    height = job["options"]["height_mm"]
    voxel = max(0.12, height * th["voxel_size_fraction_of_height"])
    parts, recs = rebuild(job_id, src, out / "pass0", voxel=voxel, mode="auto", smooth=3, label="1차")
    history = []
    latest_override: dict[str, int] = {}     # part -> pass whose output was rejected
    sil = silhouettes(parts, masks, out / "silhouette", "pass0") if masks else {}
    history.append(dict(pass_=0, silhouette=sil))
    for it in range(1, 3):
        if not masks or not any(r["excess_ratio"] > 0.015 for r in sil.values()):
            break
        jobs.progress(job_id, "editable", 50 + it * 10, f"사진 실루엣 보정 {it}회차 (비주얼 헐 카빙)")
        carved, carve_rep = visual_hull.carve(parts, masks, _bounds(parts), dilate_px=max(3, 7 - 2 * it))
        changed = {r["part"] for r in carve_rep.get("parts", []) if r["status"] == "CARVED" and r["removed_ratio"] > 0.002}
        history[-1]["carve"] = carve_rep
        if not changed:
            break
        again, rec2 = rebuild(job_id, {n: carved[n] for n in changed}, out / f"pass{it}", voxel=voxel, mode="solid",
                              smooth=2, label=f"{it}차 보정")
        # Verification loop: a correction is adopted only if the new part is still a clean closed solid.
        adopted, rejected = [], []
        for n in changed:
            r2 = rec2[n]
            if r2["metrics"]["closed_manifold"] and r2["metrics"]["shells"] == 1 and r2["self_intersection"]["status"] == "PASS":
                parts[n], recs[n] = again[n], r2
                adopted.append(n)
            else:
                rejected.append(dict(part=n, reason="integrity check failed after correction; previous pass kept",
                                     self_intersection=r2["self_intersection"], closed=r2["metrics"]["closed_manifold"]))
                latest_override[n] = it
        sil = silhouettes(parts, masks, out / "silhouette", f"pass{it}")
        history.append(dict(pass_=it, silhouette=sil, adopted=sorted(adopted), rejected=rejected))
        if not adopted:
            break
    # assemble native scene (quad meshes) + OBJ round-trip
    jobs.progress(job_id, "editable", 85, "Blender 조립 · BLEND/OBJ 저장 · 왕복 검사")
    latest = {}
    for folder in sorted(out.glob("pass*"), key=lambda f: int(f.name[4:])):
        for part_dir in folder.iterdir():
            rejected_at = latest_override.get(part_dir.name)
            if (part_dir / "part.blend").is_file() and not (rejected_at is not None and int(folder.name[4:]) >= rejected_at):
                latest[part_dir.name] = part_dir
    asm = out / "native"
    ops = [dict(op="append", blend=os.path.relpath(latest[n] / "part.blend", asm), objects=[n]) for n in parts]
    ops += [dict(op="export", blend="editable.blend", obj="editable.obj"), dict(op="roundtrip", obj="editable.obj")]
    native = run_plan(asm, ops, budget_s=settings.part_seconds)
    roundtrip = next(o for o in native["ops"] if o["op"] == "roundtrip")
    meshio.save_parts(parts, out / "parts", dict(stage="editable"))
    (out / "editable.glb").write_bytes(meshio.export_glb(parts))
    report = gates(job, parts, recs, sil, masks, roundtrip, height)
    report.update(history=history, voxel_mm=voxel, geometry_sha256=meshio.geometry_digest(parts),
                  split_geometry_sha256=json.loads((root / "split" / "parts" / "parts.json").read_text())["geometry_sha256"])
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    blocking_ok = report["blocking_pass"]
    return dict(status="review" if blocking_ok else "blocked", summary=dict(
        parts=len(parts), closed=report["closed_parts"], gates={g["id"]: g["status"] for g in report["gates"]},
        min_iou=report["min_iou"]))


def gates(job, parts, recs, sil, masks, roundtrip, height) -> dict:
    crit = criteria()["editable"]
    th = crit["thresholds"]
    rows = []
    for name, m in parts.items():
        a = audit(m)
        r = recs.get(name, {})
        met = r.get("metrics", {})
        rows.append(dict(part=name, audit=a, blender=met, closure_mode=r.get("closure_mode"), retopology=r.get("retopology"),
                         self_intersection=r.get("self_intersection", {}).get("status", "NOT_VERIFIED"),
                         reference_deviation=r.get("reference_deviation"), voxel_mm=r.get("voxel_mm")))
    closed = sum(bool(x["audit"]["closed"] and x["blender"].get("closed_manifold")) for x in rows)
    min_iou = min((v["iou"] for v in sil.values()), default=None)
    ymax = max(m.bounds[1][1] for m in parts.values())
    ymin = min(m.bounds[0][1] for m in parts.values())
    status = {
        "all_parts_closed": "PASS" if rows and closed == len(rows) else "FAIL",
        "single_shell_per_part": "PASS" if rows and all(x["blender"].get("shells") == 1 for x in rows) else "FAIL",
        "semantic_split_approved": "PASS" if job["stages"]["split"]["status"] == "approved" else "FAIL",
        "photo_silhouette": ("BLOCKED" if not masks else "PASS" if min_iou >= th["photo_silhouette_min_iou"] else "FAIL"),
        "self_intersection_screen": ("PASS" if all(x["self_intersection"] == "PASS" for x in rows)
                                     else "FAIL" if any(x["self_intersection"] == "FAIL" for x in rows) else "NOT_VERIFIED"),
        "quad_dominant_topology": "PASS" if all(x["blender"].get("quad_ratio", 0) >= th["min_quad_ratio"] for x in rows) else "FAIL",
        "native_roundtrip": roundtrip.get("verdict", "FAIL"),
        "scale_and_axis": "PASS" if abs((ymax - ymin) - height) <= 0.03 * height else "FAIL",
        "polycount_budget": "NOT_VERIFIED",
        "zbrush_sculpt_roundtrip": "NOT_VERIFIED",
    }
    gate_rows = [dict(id=g["id"], status=status[g["id"]], blocking=g["blocking"], pages=g["pages"], rule=g["rule"])
                 for g in crit["gates"]]
    blocking_pass = all(g["status"] == "PASS" for g in gate_rows if g["blocking"])
    return dict(parts=rows, closed_parts=closed, silhouette=sil, min_iou=min_iou, gates=gate_rows,
                blocking_pass=blocking_pass, quads_total=sum(x["blender"].get("quads", 0) for x in rows),
                release_approved=False,
                note="Machine gates only. ZBrush sculpt round-trip and design approval are human steps (PDF p.96-99).")


def approve(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    report = json.loads((root / "editable" / "report.json").read_text(encoding="utf-8"))
    if not report["blocking_pass"]:
        raise PermissionError("blocking editable gates are not all PASS")
    current = meshio.geometry_digest(meshio.load_saved(root / "editable" / "parts"))
    if current != report["geometry_sha256"]:
        raise PermissionError("editable geometry changed after the report")
    return dict(status="approved", approved_geometry_sha256=current)


def load_editable(root: Path):
    return meshio.load_saved(root / "editable" / "parts")
