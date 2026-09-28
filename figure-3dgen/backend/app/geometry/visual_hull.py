"""Photo-grounded correction: intersect closed parts with the visual hull of the approved views.

Only *excess* (model outside the photo silhouette) can be removed this way. Missing volume
(photo silhouette not covered by the model) is reported as a deficit for review, never invented.
"""
from __future__ import annotations

import manifold3d as md
import numpy as np
import shapely
import shapely.affinity
import trimesh
from scipy import ndimage
from shapely.geometry import box

from .render import VIEWS, frame


def mask_polygon(mask: np.ndarray, simplify_px: float = 0.75):
    """Union of horizontal pixel runs -> (multi)polygon in pixel coordinates."""
    rects = []
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            continue
        d = np.diff(np.r_[0, row.astype(np.int8), 0])
        for a, b in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
            rects.append(box(a, y, b, y + 1))
    if not rects:
        return None
    return shapely.unary_union(rects).simplify(simplify_px, preserve_topology=True)


def solid(mesh: trimesh.Trimesh) -> md.Manifold:
    m = md.Manifold(md.Mesh64(np.array(mesh.vertices, dtype=np.float64, order="C"), np.array(mesh.faces, dtype=np.uint64, order="C")))
    if m.status() != md.Error.NoError:
        raise ValueError("INVALID_SOLID")
    return m


def to_trimesh(m: md.Manifold) -> trimesh.Trimesh:
    data = m.to_mesh64()
    return trimesh.Trimesh(np.asarray(data.vert_properties)[:, :3], np.asarray(data.tri_verts), process=False)


def view_prism(mask: np.ndarray, view: str, bounds: np.ndarray, dilate_px: int) -> md.Manifold | None:
    size = mask.shape[0]
    fr = frame(bounds, size)
    grown = ndimage.binary_dilation(mask, iterations=dilate_px) if dilate_px else mask
    poly = mask_polygon(grown)
    if poly is None or poly.is_empty:
        return None
    # pixel -> (s, y - center.y) in mm
    poly = shapely.affinity.affine_transform(
        poly, [1 / fr["scale"], 0, 0, -1 / fr["scale"], -size / 2 / fr["scale"], size / 2 / fr["scale"]])
    depth = float(np.linalg.norm(bounds[1] - bounds[0])) * 2 + 10
    geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    cam, right = VIEWS[view]
    up = np.array([0, 1.0, 0])
    basis = np.column_stack([right, up, cam])
    out = None
    for g in geoms:
        if g.area < 1e-6:
            continue
        prism = trimesh.creation.extrude_polygon(g, depth)
        verts = prism.vertices.copy()
        verts[:, 2] -= depth / 2
        world = verts @ basis.T + fr["center"]
        faces = prism.faces if np.linalg.det(basis) > 0 else prism.faces[:, ::-1]
        piece = solid(trimesh.Trimesh(world, faces, process=False))
        out = piece if out is None else out + piece
    return out


def carve(parts: dict[str, trimesh.Trimesh], masks: dict[str, np.ndarray], bounds: np.ndarray,
          dilate_px: int = 6) -> tuple[dict[str, trimesh.Trimesh], dict]:
    hull = None
    used = []
    for view, mask in masks.items():
        prism = view_prism(mask, view, bounds, dilate_px)
        if prism is None:
            continue
        hull = prism if hull is None else hull ^ prism
        used.append(view)
    if hull is None:
        return parts, dict(status="NOT_APPLIED", reason="NO_APPROVED_SILHOUETTES")
    out, rows = {}, []
    for name, mesh in parts.items():
        s = solid(mesh)
        carved = s ^ hull
        before, after = s.volume(), carved.volume()
        pieces = carved.decompose()
        if after <= before * 0.3 or not pieces:
            # Removing most of a part means misalignment / wrong view, not excess: keep and report.
            out[name] = mesh
            rows.append(dict(part=name, status="REJECTED", removed_ratio=round(1 - after / max(before, 1e-9), 4),
                             reason="CARVE_WOULD_REMOVE_MOST_OF_PART"))
            continue
        largest = max(pieces, key=lambda p: p.volume())
        out[name] = to_trimesh(largest)
        rows.append(dict(part=name, status="CARVED", removed_ratio=round(1 - largest.volume() / max(before, 1e-9), 4),
                         pieces_dropped=len(pieces) - 1))
    return out, dict(status="APPLIED", views=used, dilate_px=dilate_px, parts=rows)
