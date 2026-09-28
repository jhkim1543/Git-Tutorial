import numpy as np
import pytest
import trimesh

from app.config import criteria
from app.geometry import insertion, joints, mold_checks, segmentation, visual_hull
from app.geometry.audit import audit
from app.geometry.render import model_mask, compare


def split_sphere(noise, seed=1):
    rng = np.random.default_rng(seed)
    s = trimesh.creation.icosphere(4, radius=10)
    c = s.triangles_center
    side = (c[:, 0] + (rng.normal(0, noise, len(c)) if noise else 0)) > 0
    return s.submesh([np.flatnonzero(side)], append=True), s.submesh([np.flatnonzero(~side)], append=True)


def test_audit_open_vs_closed():
    closed = trimesh.creation.icosphere(3)
    assert audit(closed)["closed"]
    opened = closed.copy()
    opened.update_faces(np.arange(len(opened.faces)) > 5)
    a = audit(opened)
    assert not a["closed"] and a["open_edges"] > 0


def test_seam_jaggedness_separates_clean_and_ragged_cuts():
    clean = segmentation.analyze(dict(zip("AB", split_sphere(0.0))), 100)["seams"][0]
    ragged = segmentation.analyze(dict(zip("AB", split_sphere(1.2))), 100)["seams"][0]
    assert clean["jaggedness"] < 1.3 < 2.0 < ragged["jaggedness"]
    assert clean["dihedral_median_deg"] < 10       # the surface continues across the seam


def test_clean_resplit_smooths_boundary():
    a, b = split_sphere(1.2)
    combined, labels, info = segmentation.clean_resplit(a, b)
    A = combined.submesh([np.flatnonzero(labels == 0)], append=True)
    B = combined.submesh([np.flatnonzero(labels == 1)], append=True)
    after = segmentation.analyze({"A": A, "B": B}, 100)["seams"][0]["jaggedness"]
    assert after < 1.4 and info["changed_area_ratio"] < 0.2


def test_split_judge_prior_and_fit():
    judge = segmentation.SplitJudge()
    patch = dict(dihedral_median_deg=1.0, share_a=1.0, share_b=0.3, smaller_area_share=0.01, jaggedness=2.5, seam_mm=40)
    crease = dict(dihedral_median_deg=55.0, share_a=0.3, share_b=0.2, smaller_area_share=0.2, jaggedness=1.1, seam_mm=40)
    assert judge.decide(patch)["action"] == "merge"
    assert judge.decide(crease)["action"] == "keep"
    # human says "same smooth surface but keep separate" several times -> the model moves toward keep
    keep_smooth = dict(dihedral_median_deg=2.0, share_a=0.6, share_b=0.5, smaller_area_share=0.1, jaggedness=1.1, seam_mm=40)
    fitted = segmentation.SplitJudge.fit([keep_smooth] * 6 + [patch] * 3, [0] * 6 + [1] * 3)
    assert fitted.prob_merge(keep_smooth) < judge.prob_merge(keep_smooth)
    assert fitted.meta["trained_on"] == 9


def test_visual_hull_removes_only_excess():
    truth = trimesh.creation.box((20, 40, 20))
    truth.apply_translation([0, 20, 0])
    bloated = trimesh.creation.box((30, 40, 20))
    bloated.apply_translation([0, 20, 0])
    bounds = truth.bounds
    masks = {v: model_mask({"t": truth}, v, 256, bounds) for v in ("front", "right")}
    carved, rep = visual_hull.carve({"p": bloated}, masks, bloated.bounds, dilate_px=2)
    assert rep["parts"][0]["status"] == "CARVED"
    assert carved["p"].extents[0] < 23 and audit(carved["p"])["closed"]
    before = compare(model_mask({"p": bloated}, "front", 256, bloated.bounds), masks["front"])
    after = compare(model_mask(carved, "front", 256, carved["p"].bounds), masks["front"])
    assert after["iou"] > before["iou"]


def assembly():
    body = trimesh.creation.box((20, 20, 20))
    body.apply_translation([0, 10, 0])
    head = trimesh.creation.icosphere(3, radius=9)
    head.apply_translation([0, 28.5, 0])
    return joints.resolve_overlaps({"body": body, "head": head})[0]


def test_joint_build_integrated_and_insertion():
    parts = assembly()
    ifs = joints.contact_graph(parts)
    assert len(ifs) == 1
    plans = joints.plan_joints(parts, ifs, criteria()["moldable"]["joint_defaults_assumption"])
    out, rep = joints.build_all(parts, plans)
    assert rep["status"] == "BUILT" and rep["built"] == 1
    ev = rep["joints"][0]["evidence"]
    assert ev["pin_integrated"] and ev["socket_cut"]
    assert all(audit(m)["closed"] for m in out.values())
    p = rep["joints"][0]["plan"]
    ok = insertion.sweep(out[p["male"]], [out[p["female"]]], -np.asarray(p["axis"]) * (p["length_mm"] + 1))
    bad = insertion.sweep(out[p["male"]], [out[p["female"]]], np.array([3.0, 0, 0]))
    assert ok["status"] == "PASS" and bad["status"] == "FAIL"


def test_joint_blocked_when_no_room():
    parts = assembly()
    ifs = joints.contact_graph(parts)
    plan = joints.plan_joints(parts, ifs, criteria()["moldable"]["joint_defaults_assumption"])[0]
    plan.update(radius_mm=30.0)            # larger than either body
    out, rep = joints.build_all(parts, [plan])
    assert out is None and rep["status"] == "BLOCKED"


def test_mold_checks_flag_thin_plate():
    plate = trimesh.creation.box((30, 30, 0.8))
    plate.apply_translation([0, 0.4, 0])
    res = mold_checks.check_parts({"plate": plate}, criteria()["moldable"]["materials"]["PVC_INJECTION"], samples=800)
    row = res["parts"][0]
    assert row["min_wall"] == "FAIL" and row["sharp_points"] == "FAIL"
    thick = trimesh.creation.box((30, 30, 4))
    thick.apply_translation([0, 2, 0])
    res = mold_checks.check_parts({"b": thick}, criteria()["moldable"]["materials"]["PVC_INJECTION"], samples=800)
    assert res["parts"][0]["min_wall"] == "PASS" and res["stability"]["status"] == "PASS"
    assert res["legal_line"]["status"] == "PASS"


def test_undercut_detects_cross_holes():
    block = trimesh.creation.box((20, 20, 20))
    holes = []
    for axis in ([1, 0, 0], [0, 1, 0], [0, 0, 1]):
        c = trimesh.creation.cylinder(radius=4, height=30, sections=32)
        c.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], axis))
        holes.append(c)
    shape = trimesh.boolean.difference([block, trimesh.boolean.union(holes, engine="manifold")], engine="manifold")
    assert mold_checks.undercut(shape, 1500)["undercut_ratio"] > 0.02     # no pull direction frees every face
    assert mold_checks.undercut(block, 800)["undercut_ratio"] == 0
