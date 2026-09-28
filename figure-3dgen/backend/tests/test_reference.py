import csv
import hashlib

import trimesh

from app.config import criteria
from app.geometry import joints
from app.reference import cly
from app.reference.analyze import analyze_assembly, build_library


def reference_pair(profile):
    body = trimesh.creation.box((20, 20, 20))
    body.apply_translation([0, 10, 0])
    head = trimesh.creation.icosphere(3, radius=11)
    head.apply_translation([0, 31, 0])
    parts, _ = joints.resolve_overlaps({"body": body, "head": head})
    plan = dict(interface_id="X", male="body", female="head", origin=[0, 20, 0], axis=[0, 1, 0], profile=profile,
                radius_mm=2.0, length_mm=4.0, clearance_mm=0.15, end_clearance_mm=0.4, wall_mm=1.2)
    solids = {n: joints.solid(m) for n, m in parts.items()}
    m, f, _ = joints.construct(solids, plan)
    return {"body": joints.mesh_of(m), "head": joints.mesh_of(f)}


def test_analyzer_finds_male_female_and_measures():
    res = analyze_assembly(reference_pair("round"))
    pins = [j for j in res["joints"] if j["kind"] == "pin_socket"]
    assert len(pins) == 1
    j = pins[0]
    assert (j["male"], j["female"], j["archetype"]) == ("body", "head", "round_pin")
    assert abs(j["clearance_median_mm"] - 0.15) < 0.03
    assert abs(j["interface_diameter_mm"] - 4.0) < 0.3
    assert abs(j["socket_wall_p05_mm"] - 1.2) < 0.2
    assert j["withdraw_axis"][1] < -0.95       # the pin leaves the head downwards


def test_analyzer_keyed_profile_and_library():
    keyed = analyze_assembly(reference_pair("d_key"))
    assert [j["archetype"] for j in keyed["joints"] if j["kind"] == "pin_socket"] == ["keyed_pin"]
    lib = build_library({"a": keyed, "b": analyze_assembly(reference_pair("round"))}, min_samples=2)
    assert lib["status"] == "READY" and set(lib["summary"]) == {"keyed_pin", "round_pin"}
    parts = reference_pair("round")
    ifs = [dict(id="I0", a="body", b="head", origin=[0, 20, 0], normal_ab=[0, 1, 0], area_mm2=50, room_radius_mm=6,
                diameter_mm=12, planarity_rms_mm=0, min_width_mm=12)]
    plans = joints.plan_joints(parts, ifs, criteria()["moldable"]["joint_defaults_assumption"], lib)
    assert plans[0]["parameter_source"].startswith("s3_library") and abs(plans[0]["clearance_mm"] - 0.15) < 0.03


def test_manifest_verification(tmp_path):
    data = b"fake cly payload Freeform Plus/146 V2014.2 mm"
    (tmp_path / "01_x_x.cly").write_bytes(data)
    manifest = tmp_path / "m.csv"
    with manifest.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Index", "SampleId", "Filename", "Bytes", "SHA256"])
        w.writeheader()
        w.writerow(dict(Index=1, SampleId="x", Filename="01_x_x.cly", Bytes=len(data), SHA256=hashlib.sha256(data).hexdigest()))
        w.writerow(dict(Index=2, SampleId="y", Filename="02_y_y.cly", Bytes=1, SHA256="0" * 64))
    res = cly.verify_manifest(manifest, tmp_path)
    assert res["passed"] == 1 and res["missing"] == 1
    head = cly.scan_header(tmp_path / "01_x_x.cly")
    assert head["program"].startswith("Freeform") and head["unit_hint"] == "mm" and head["format_verified"] is False


def test_shipped_manifest_has_50_distinct_samples():
    from app.config import ROOT
    rows = list(csv.DictReader((ROOT / "reference" / "s3_50_manifest.csv").open(encoding="utf-8-sig")))
    assert len(rows) == 50 and len({r["SampleId"] for r in rows}) == 50
    assert all(len(r["SHA256"]) == 64 for r in rows)
