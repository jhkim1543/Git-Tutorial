"""Stage 5 — assembly graph + male/female joint plans + Astra joint concept sheets.

Every contact interface of the (overlap-resolved) approved Editable gets exactly one decision:
merge (e.g. eyes into the face: PAD-printed, not a separate molded piece, PDF p.31-32) or a joint.
Astra draws a male/female concept sheet per joint and returns a structured spec; the geometry that
follows uses the *approved spec*, snapped to the joint library — the image is design guidance.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .. import astra, jobs
from ..config import criteria, joint_library
from ..geometry import joints, meshio
from ..geometry.render import VIEWS, png_bytes, render
from .editable import load_editable

SHEET_PROMPT = (
    "Draw an engineering concept sheet (white background, clean line art with light grey shading, labelled in Korean) for "
    "ONE male/female joint between two parts of this collectible figure. Show: (1) the two parts separated along the "
    "insertion axis with an arrow, (2) a cross-section through the {archetype} with the MALE part '{male}' (pin integrated "
    "into its body) and the FEMALE part '{female}' (socket cut into its body, with a wall around it), (3) a small assembled "
    "view. Mark clearance, wall and anti-rotation feature if any. Interface diameter about {diameter} mm, pin radius "
    "{radius} mm, pin length {length} mm. Do not invent brand marks. Also answer with the JSON spec when asked.")


def plan(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    job = jobs.load(job_id)
    jobs.require(job, "editable", "approved")
    out = root / "joints"
    out.mkdir(exist_ok=True)
    editable_parts = load_editable(root)
    jobs.progress(job_id, "joints", 5, "겹침 해소 · 병합 후보")
    first, _ = joints.resolve_overlaps(editable_parts)
    merges = []
    for it in joints.contact_graph(first):
        small, big = sorted((it["a"], it["b"]), key=lambda n: first[n].volume)
        embedded = it["area_mm2"] / max(first[small].area, 1e-9)
        if first[small].volume < 0.03 * first[big].volume and embedded > 0.2:
            merges.append(dict(parts=[small, big], into=big, reason="small embedded detail: merge for molding "
                               "(DECO/PAD handles it, PDF p.31-32)", embedded_ratio=round(embedded, 3), source="rule"))
    jobs.progress(job_id, "joints", 15, "병합 적용 후 생산 분할 · 접점 그래프")
    merged, _, _ = joints.apply_merges(editable_parts, merges)
    parts, partition = joints.resolve_overlaps(merged)
    interfaces = joints.contact_graph(parts)
    defaults = criteria()["moldable"]["joint_defaults_assumption"]
    library = joint_library()
    plans = joints.plan_joints(parts, interfaces, defaults, library)
    sheets = {}
    bounds = np.array([np.min([m.bounds[0] for m in parts.values()], 0), np.max([m.bounds[1] for m in parts.values()], 0)])
    center = bounds.mean(0)
    front = root / "views" / "front.png"
    (out / "sheets").mkdir(exist_ok=True)
    for i, p in enumerate(plans):
        view = max(VIEWS, key=lambda v: float(np.dot(np.asarray(p["origin"]) - center, VIEWS[v][0])))
        crop = render(parts, view, size=448, bounds=bounds, highlight={p["male"], p["female"]})
        (out / "sheets" / f"{p['interface_id']}-context.png").write_bytes(png_bytes(crop))
        if not astra.available():
            sheets[p["interface_id"]] = dict(status="BLOCKED", reason="OPENAI_API_KEY not configured")
            continue
        jobs.progress(job_id, "joints", 30 + 60 * i / max(len(plans), 1), f"Astra 암수 개념도 {i + 1}/{len(plans)}")
        images = [("context-highlight.png", png_bytes(crop))] + ([("approved-front.png", front.read_bytes())] if front.is_file() else [])
        it = next(x for x in interfaces if x["id"] == p["interface_id"])
        try:
            img = astra.generate_image(SHEET_PROMPT.format(archetype=p["archetype"].replace("_", " "), male=p["male"],
                                                           female=p["female"], diameter=it["diameter_mm"],
                                                           radius=p["radius_mm"], length=p["length_mm"]),
                                       images, cache=root / "cache" / "astra", size="1536x1024")
            (out / "sheets" / f"{p['interface_id']}.png").write_bytes(img["png"])
            spec = astra.review_json(
                "Give the joint specification you drew for interface " + p["interface_id"] + ". Candidate plan (rule-based, "
                "may be changed): " + json.dumps(p, ensure_ascii=False) + " Interface evidence: " + json.dumps(it, ensure_ascii=False),
                images + [("concept-sheet.png", img["png"])], astra.JOINT_SCHEMA, name="joint_spec", cache=root / "cache" / "astra")
            sheets[p["interface_id"]] = dict(status="DONE", image=f"{p['interface_id']}.png", spec=spec["result"],
                                             provenance=[img["provenance"], spec["provenance"]])
            apply_spec(p, spec["result"], parts, defaults)
        except astra.AstraError as exc:
            sheets[p["interface_id"]] = dict(status="ERROR", reason=str(exc))
    doc = dict(partition=partition, partition_geometry_sha256=meshio.geometry_digest(parts), interfaces=interfaces,
               merges=merges, plans=plans, sheets=sheets, library_status=library.get("status", "EMPTY"),
               editable_geometry_sha256=job["stages"]["editable"].get("approved_geometry_sha256"))
    (out / "plan.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(status="review", summary=dict(interfaces=len(interfaces), joints=len(plans), merges=len(merges),
                                               sheets=sum(s.get("status") == "DONE" for s in sheets.values())))


def apply_spec(p: dict, spec: dict, parts, defaults) -> None:
    """Adopt Astra's spec only inside the library's choices; keep the rule plan when unsure."""
    p["astra_spec"] = spec
    if spec.get("confidence", 0) < 0.6:
        p["needs_human"] = True
        return
    if {spec["male_part"], spec["female_part"]} == {p["male"], p["female"]} and spec["male_part"] != p["male"]:
        p["male"], p["female"] = p["female"], p["male"]
        p["axis"] = (-np.asarray(p["axis"])).round(6).tolist()
    if spec["archetype"] in ("round_pin", "d_key_pin", "neck_plug"):
        p["archetype"] = spec["archetype"]
        p["profile"] = "round" if spec["archetype"] == "round_pin" else "d_key"
    elif spec["archetype"] in ("merge_parts", "glue_face"):
        p["needs_human"] = True        # geometry-only merge/glue must be decided by a person
    cls = next((c for c in defaults["size_classes"] if c["class"] == spec["size_class"]), None)
    if cls:
        p["size_class"] = cls["class"]
        p["radius_mm"] = min(p["radius_mm"], cls["pin_radius_mm"]) if p.get("radius_mm") else cls["pin_radius_mm"]
        p["length_mm"] = cls["pin_length_mm"]


def approve(job_id: str, edits: dict) -> dict:
    root = jobs.job_dir(job_id)
    doc = json.loads((root / "joints" / "plan.json").read_text(encoding="utf-8"))
    by_id = {p["interface_id"]: p for p in doc["plans"]}
    for e in edits.get("plans", []):
        p = by_id.get(e.get("interface_id"))
        if not p:
            raise ValueError(f"unknown interface {e.get('interface_id')}")
        if e.get("swap"):
            p["male"], p["female"] = p["female"], p["male"]
            p["axis"] = (-np.asarray(p["axis"])).round(6).tolist()
        if e.get("archetype") in ("round_pin", "d_key_pin", "neck_plug"):
            p["archetype"] = e["archetype"]
            p["profile"] = "round" if e["archetype"] == "round_pin" else "d_key"
        if e.get("merge"):
            doc["merges"].append(dict(parts=[p["male"], p["female"]], into=p["female"], reason="human decision",
                                      interface_id=p["interface_id"], source="human"))
            p["status"] = "merged"
    doc["plans"] = [p for p in doc["plans"] if p.get("status") != "merged"]
    covered = {p["interface_id"] for p in doc["plans"]} | {m.get("interface_id") for m in doc["merges"]}
    missing = {i["id"] for i in doc["interfaces"]} - covered
    if missing:
        raise ValueError(f"interfaces without a decision: {sorted(missing)}")
    for p in doc["plans"]:
        p["status"] = "approved"
    doc["approved"] = True
    (root / "joints" / "plan.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(status="approved", summary=dict(joints=len(doc["plans"]), merges=len(doc["merges"])))


def load_plan(root: Path) -> dict:
    return json.loads((root / "joints" / "plan.json").read_text(encoding="utf-8"))
