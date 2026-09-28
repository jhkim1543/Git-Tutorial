"""Stage 6 — Moldable, DERIVED from the approved Editable snapshot (never generated from scratch).

editable (hash-checked) -> overlap resolution -> approved merges (union) -> integrated male/female joints
-> interference + insertion sweeps -> PDF mold checks -> fix loop -> Blender native export + round-trip.
"""
from __future__ import annotations

import json

import numpy as np
import trimesh

from .. import jobs
from ..blender.runner import run_plan
from ..config import criteria, settings
from ..geometry import insertion, joints, meshio, mold_checks
from ..geometry.audit import audit
from .editable import load_editable
from .joints_stage import load_plan


def build(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    job = jobs.load(job_id)
    jobs.require(job, "joints", "approved")
    crit = criteria()["moldable"]
    material_key = job["options"].get("material", "PVC_ROTO")
    material = crit["materials"][material_key]
    out = root / "moldable"
    out.mkdir(exist_ok=True)
    editable = load_editable(root)
    approved_hash = job["stages"]["editable"].get("approved_geometry_sha256")
    derived = meshio.geometry_digest(editable) == approved_hash
    if not derived:
        raise PermissionError("editable geometry does not match the approved snapshot")
    doc = load_plan(root)
    jobs.progress(job_id, "moldable", 5, "병합(원본 솔리드 합집합) · 겹침 해소 · 접점 재확인")
    merged0, name_of, merge_rows = joints.apply_merges(editable, doc["merges"])
    merged, _ = joints.resolve_overlaps(merged0)
    contacts = joints.contact_graph(merged)
    plans = []
    for p in doc["plans"]:
        q = dict(p, male=name_of[p["male"]], female=name_of[p["female"]])
        if q["male"] != q["female"]:
            plans.append(q)
    planned_pairs = {frozenset((p["male"], p["female"])) for p in plans}
    unplanned = sorted({f"{c['a']}~{c['b']}" for c in contacts if frozenset((c["a"], c["b"])) not in planned_pairs})
    if unplanned:
        report = dict(status="BLOCKED", reason="UNPLANNED_INTERFACES", unplanned=unplanned, merges=merge_rows)
        (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        return dict(status="blocked", summary=dict(unplanned=unplanned))
    jobs.progress(job_id, "moldable", 20, f"암수 {len(plans)}개 생성 (핀 합집합 · 소켓 차집합)")
    built, jrep = joints.build_all(merged, plans, on_row=lambda i, n, r: jobs.progress(
        job_id, "moldable", 20 + 35 * i / max(n, 1), f"암수 {i}/{n} {r['interface_id']} {r['status']}"))
    fix_loop = [dict(step="joint offset/radius retries", detail="each joint tried at 6 axial offsets x 3 radius scales")]
    if built is None:
        report = dict(status="BLOCKED", joints=jrep, merges=merge_rows, fix_loop=fix_loop, derived_from_editable=derived)
        (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
        return dict(status="blocked", summary=dict(joints_built=jrep["built"], joints_required=jrep["required"]))
    # insertion sweeps: the male side moves as a sub-assembly (everything jointed to it except the female side)
    sweeps = []
    edges = [(r["plan"]["male"], r["plan"]["female"]) for r in jrep["joints"]]
    for i, row in enumerate(jrep["joints"]):
        p = row["plan"]
        jobs.progress(job_id, "moldable", 55 + 20 * i / max(len(plans), 1), f"삽입 경로 검사 {p['interface_id']}")
        moving = side_of(p["male"], [e for e in edges if e != (p["male"], p["female"])])
        if p["female"] in moving:
            sweeps.append(dict(interface_id=p["interface_id"], status="NOT_VERIFIED", reason="JOINT_GRAPH_CYCLE_NEEDS_ASSEMBLY_ORDER"))
            continue
        dist = p["length_mm"] + p["end_clearance_mm"] + 0.5
        group = trimesh.util.concatenate([built[n] for n in sorted(moving)])
        res = insertion.sweep(group, [built[n] for n in built if n not in moving], -np.asarray(p["axis"]) * dist, seconds=150)
        sweeps.append(dict(interface_id=p["interface_id"], moving_group=sorted(moving), **res))
    jobs.progress(job_id, "moldable", 78, "몰더블 조건 검사 (두께·언더컷·샤프포인트·자립·표기)")
    checks = mold_checks.check_parts(built, material)
    # native export
    jobs.progress(job_id, "moldable", 88, "Blender BLEND/OBJ 저장 · 왕복 검사")
    native = out / "native"
    native.mkdir(exist_ok=True)
    ops = []
    for n, m in built.items():
        np.savez_compressed(native / f"{n}.npz", vertices=np.asarray(m.vertices), faces=np.asarray(m.faces))
        ops.append(dict(op="load", name=n, file=f"{n}.npz"))
    ops += [dict(op="export", blend="moldable.blend", obj="moldable.obj"), dict(op="roundtrip", obj="moldable.obj")]
    nat = run_plan(native, ops, budget_s=settings.part_seconds)
    roundtrip = next(o for o in nat["ops"] if o["op"] == "roundtrip")
    stl = out / "stl"
    stl.mkdir(exist_ok=True)
    for n, m in built.items():
        m.export(stl / f"{n}.stl")
    meshio.save_parts(built, out / "parts", dict(stage="moldable"))
    colors = {}
    male = {r["plan"]["male"] for r in jrep["joints"]}
    female = {r["plan"]["female"] for r in jrep["joints"]}
    for i, n in enumerate(built):
        colors[n] = [90, 150, 250, 255] if n in male and n not in female else [250, 150, 90, 255] if n in female and n not in male else meshio.part_color(i)
    (out / "moldable.glb").write_bytes(meshio.export_glb(built, colors))
    report = gates(job, built, jrep, sweeps, checks, roundtrip, doc, merge_rows, derived, material_key)
    report.update(fix_loop=fix_loop)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    return dict(status="done" if report["blocking_pass"] else "blocked",
                summary=dict(parts=len(built), joints=jrep["built"], gates={g["id"]: g["status"] for g in report["gates"]}))


def side_of(start: str, edges: list[tuple[str, str]]) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo += [b for a, b in edges if a == n] + [a for a, b in edges if b == n]
    return seen


def gates(job, parts, jrep, sweeps, checks, roundtrip, doc, merge_rows, derived, material_key) -> dict:
    crit = criteria()["moldable"]
    audits = {n: audit(m) for n, m in parts.items()}
    rows = checks["parts"]

    def all_status(key):
        vals = [r.get(key) for r in rows if key in r]
        if not vals:
            return "NOT_VERIFIED"
        return "FAIL" if "FAIL" in vals else "NOT_VERIFIED" if "NOT_VERIFIED" in vals else "PASS"
    sheets = doc.get("sheets", {})
    status = {
        "derived_from_editable": "PASS" if derived else "FAIL",
        "all_parts_closed": "PASS" if audits and all(a["closed"] for a in audits.values()) else "FAIL",
        "assembly_graph_coverage": "PASS" if doc.get("approved") and len(doc["plans"]) + len(doc["merges"]) >= len(doc["interfaces"]) else "FAIL",
        "male_female_pairs": "PASS" if jrep["built"] == jrep["required"] and all(r["evidence"]["pin_integrated"] and r["evidence"]["socket_cut"] for r in jrep["joints"]) else "FAIL",
        "assembled_interference": "PASS" if not jrep.get("interference") else "FAIL",
        "insertion_path": ("PASS" if sweeps and all(s["status"] == "PASS" for s in sweeps) else
                           "NOT_VERIFIED" if any(s["status"] == "NOT_VERIFIED" for s in sweeps) else "FAIL") if jrep["required"] else "PASS",
        "min_wall_thickness": all_status("min_wall"),
        "thickness_uniformity": "NOT_VERIFIED",
        "abs_max_thickness": all_status("abs_max_thickness") if material_key.startswith("ABS") else "PASS",
        "undercut": all_status("undercut_gate"),
        "draft": "NOT_VERIFIED",
        "sharp_points": all_status("sharp_points"),
        "self_standing": checks["stability"]["status"],
        "legal_line_area": {"PASS": "PASS", "WARN": "PASS", "FAIL": "FAIL"}.get(checks["legal_line"]["status"], "NOT_VERIFIED"),
        "small_parts": "FAIL" if any(r["small_part"]["fits_cylinder"] for r in rows) else "PASS",
        "s3_grounded_joint_parameters": "PASS" if doc.get("library_status") == "READY" else "NOT_VERIFIED",
        "process_and_physical_approval": "NOT_VERIFIED",
    }
    gate_rows = [dict(id=g["id"], status=status[g["id"]], blocking=g["blocking"], pages=g["pages"], rule=g["rule"])
                 for g in crit["gates"]]
    gate_rows.append(dict(id="joint_concept_sheets", blocking=False, pages=[],
                          status="PASS" if sheets and all(s.get("status") == "DONE" for s in sheets.values()) else
                          ("BLOCKED" if any(s.get("status") == "BLOCKED" for s in sheets.values()) else "NOT_VERIFIED"),
                          rule="Astra male/female concept sheet + spec per joint, human-approved"))
    gate_rows.append(dict(id="native_roundtrip", blocking=True, pages=[97, 109], status=roundtrip.get("verdict", "FAIL"),
                          rule="Blender OBJ export -> reimport keeps names, polygons, closure"))
    blocking_pass = all(g["status"] == "PASS" for g in gate_rows if g["blocking"])
    return dict(status="PASS_MACHINE_GATES" if blocking_pass else "BLOCKED", gates=gate_rows, blocking_pass=blocking_pass,
                parts=[dict(part=n, audit=audits[n]) for n in parts], joints=jrep, insertion=sweeps, checks=checks,
                merges=merge_rows, material=material_key, release_approved=False,
                note="Machine gates only. Mold engineer / print / copycast approval is a human step (PDF p.59, 64-66, 104).")
