"""Synthetic figure used by tests and the offline demo. Explicitly NOT a real figure.

truth(): closed primitives = what the 'photo' shows (used to render the approved views).
ai_glb(): what an AI generator might output — open parts, a head cut into two ragged patches,
          a cape cut into three patches, an eye cut into two fragments, surface noise.
"""
from __future__ import annotations

import numpy as np
import trimesh

from app.geometry.render import png_bytes, render


def truth() -> dict[str, trimesh.Trimesh]:
    body = trimesh.creation.cylinder(radius=16, height=50, sections=48)
    body.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    body.apply_translation([0, 25, 0])
    head = trimesh.creation.icosphere(4, radius=22)
    head.apply_translation([0, 70, 0])
    eye = trimesh.creation.icosphere(3, radius=3.2)
    eye.apply_translation([7, 74, 20.2])
    cape = trimesh.creation.box((30, 40, 3))
    cape.apply_translation([0, 28, -17])
    return dict(body=body, head=head, eye=eye, cape=cape)


def _split(mesh: trimesh.Trimesh, key: np.ndarray, noise: float, rng, bins: list[float]) -> list[trimesh.Trimesh]:
    value = key + rng.normal(0, noise, len(key))
    idx = np.digitize(value, bins)
    return [mesh.submesh([np.flatnonzero(idx == k)], append=True) for k in range(len(bins) + 1) if (idx == k).any()]


def ai_parts(seed: int = 4) -> dict[str, trimesh.Trimesh]:
    rng = np.random.default_rng(seed)
    t = truth()
    out = {}
    body = t["body"].copy()
    body = body.subdivide().subdivide()
    keep = body.face_normals[:, 1] < 0.9        # open top where the head sits
    body.update_faces(keep)
    body.remove_unreferenced_vertices()
    out["mesh_body"] = body
    head = t["head"].copy()
    head.vertices[:, 0] *= 1.12                   # AI bloat: wider than the photo
    for i, piece in enumerate(_split(head, head.triangles_center[:, 0], 2.0, rng, [0.0])):
        out[f"mesh_head_{i}"] = piece
    eye = t["eye"].copy()
    for i, piece in enumerate(_split(eye, eye.triangles_center[:, 1] - 74, 0.6, rng, [0.0])):
        out[f"mesh_eye_{i}"] = piece
    cape = trimesh.creation.box((30, 40, 3)).subdivide().subdivide().subdivide()
    cape.apply_translation([0, 28, -17])
    cape.update_faces(cape.face_normals[:, 2] > -0.5)   # open back face
    cape.remove_unreferenced_vertices()
    for i, piece in enumerate(_split(cape, cape.triangles_center[:, 1], 1.5, rng, [22.0, 34.0])):
        out[f"mesh_cape_{i}"] = piece
    for m in out.values():
        m.vertices += rng.normal(0, 0.05, m.vertices.shape)
    return out


def ai_glb(seed: int = 4) -> bytes:
    scene = trimesh.Scene()
    for name, m in ai_parts(seed).items():
        mm = m.copy()
        mm.apply_scale(0.001)
        scene.add_geometry(mm, node_name=name, geom_name=name)
    return scene.export(file_type="glb")


def view_pngs(size: int = 512) -> dict[str, bytes]:
    t = truth()
    colors = dict(body=[70, 110, 200, 255], head=[240, 205, 170, 255], eye=[30, 30, 30, 255], cape=[180, 40, 50, 255])
    out = {}
    for view in ("front", "right", "back", "left"):
        img = render(t, view, size=size, colors=colors)
        bg = img.copy()
        from PIL import Image
        canvas = Image.new("RGB", img.size, (255, 255, 255))
        canvas.paste(bg, mask=bg.split()[3])
        out[view] = png_bytes(canvas)
    return out
