import numpy as np
import pytest
import trimesh

from app.blender.runner import BlenderFailed, run_plan
from app.config import settings
from app.geometry.audit import audit

pytestmark = pytest.mark.skipif(settings.blender_command() is None, reason="Blender / bpy not installed")


def test_open_head_and_sheet_become_closed_quads(tmp_path):
    head = trimesh.creation.icosphere(4, radius=10)
    head.update_faces(head.face_normals[:, 1] < 0.9)          # hole on top
    head.remove_unreferenced_vertices()
    sheet = trimesh.creation.icosphere(3, radius=15)
    sheet.update_faces(sheet.face_normals[:, 2] > 0.3)        # open cap = cape-like sheet
    sheet.remove_unreferenced_vertices()
    for n, m in (("head", head), ("cape", sheet)):
        np.savez(tmp_path / f"{n}.npz", vertices=m.vertices, faces=m.faces)
    ops = [dict(op="load", name="head", file="head.npz"), dict(op="load", name="cape", file="cape.npz"),
           dict(op="rebuild", part="head", voxel_mm=0.4, target_quads=1500, smooth_iterations=2),
           dict(op="rebuild", part="cape", voxel_mm=0.4, shell_mm=2.0, target_quads=1500, smooth_iterations=2),
           dict(op="save_npz", part="head", file="o_head.npz"), dict(op="save_npz", part="cape", file="o_cape.npz"),
           dict(op="export", blend="e.blend", obj="e.obj"), dict(op="roundtrip", obj="e.obj")]
    res = run_plan(tmp_path, ops, budget_s=300)
    rebuilt = {o["part"]: o for o in res["ops"] if o["op"] == "rebuild"}
    assert rebuilt["head"]["closure_mode"] == "solid" and rebuilt["cape"]["closure_mode"] == "shell"
    for name in ("head", "cape"):
        assert rebuilt[name]["metrics"]["closed_manifold"] and rebuilt[name]["metrics"]["quad_ratio"] > 0.9
        with np.load(tmp_path / f"o_{name}.npz") as raw:
            assert audit(trimesh.Trimesh(raw["vertices"], raw["faces"], process=False))["closed"]
    assert next(o for o in res["ops"] if o["op"] == "roundtrip")["verdict"] == "PASS"


def test_unknown_op_is_an_error(tmp_path):
    with pytest.raises(BlenderFailed):
        run_plan(tmp_path, [dict(op="exec_python", code="print(1)")], budget_s=120)
