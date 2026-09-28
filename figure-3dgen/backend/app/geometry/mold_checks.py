"""Moldable condition checks mapped to the DPAI PDF (see contracts/criteria.json).

All measurements are sampled screens (ray casting on the final meshes), reported with method and
sample count. They support, not replace, mold-engineer review (p.59, 64-66, 100).
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point

SMALL_PARTS_CYLINDER = dict(diameter_mm=31.7, depth_mm=57.1, source="16 CFR 1501 (external standard, not in the PDF)")


def _samples(mesh: trimesh.Trimesh, n: int, seed: int = 5):
    n = int(min(n, max(200, len(mesh.faces) * 2)))
    pts, fid = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return pts, mesh.face_normals[fid]


def thickness(mesh: trimesh.Trimesh, n: int = 1500) -> dict:
    """Inward ray from each sample to the opposite wall = local wall thickness."""
    pts, nrm = _samples(mesh, n)
    origins = pts - nrm * 1e-3
    loc, idx, _ = mesh.ray.intersects_location(origins, -nrm, multiple_hits=False)
    d = np.full(len(pts), np.nan)
    d[idx] = np.linalg.norm(loc - origins[idx], axis=1)
    ok = np.isfinite(d)
    vals = d[ok]
    if not len(vals):
        return dict(status="NOT_VERIFIED", reason="NO_RAY_HITS", samples=len(pts))
    return dict(samples=int(ok.sum()), min_mm=round(float(vals.min()), 3), p05_mm=round(float(np.percentile(vals, 5)), 3),
                p50_mm=round(float(np.median(vals)), 3), p95_mm=round(float(np.percentile(vals, 95)), 3),
                max_mm=round(float(vals.max()), 3), thin_points=np.round(pts[ok][vals < np.percentile(vals, 2)][:20], 3).tolist(),
                _values=vals, _points=pts[ok])


def thin_clusters(points: np.ndarray, values: np.ndarray, limit: float, *, radius: float = 1.5, min_members: int = 4) -> list[dict]:
    """Thin regions that are more than a single grazing ray: >= min_members thin samples within radius."""
    thin = values < limit
    if thin.sum() < min_members:
        return []
    pts, vals = points[thin], values[thin]
    tree = cKDTree(pts)
    seen, out = set(), []
    for i in np.argsort(vals):
        if i in seen:
            continue
        members = tree.query_ball_point(pts[i], radius)
        seen.update(members)
        if len(members) >= min_members:
            out.append(dict(center=np.round(pts[members].mean(0), 3).tolist(), samples=len(members),
                            min_mm=round(float(vals[members].min()), 3)))
    return out[:20]


def undercut(mesh: trimesh.Trimesh, n: int = 1500) -> dict:
    """Best two-sided pull direction: a face is moldable if it sees +d or -d without self-occlusion."""
    pts, nrm = _samples(mesh, n, seed=9)
    area_w = np.ones(len(pts))
    dirs = [np.array(v, float) for v in ([1, 0, 0], [0, 1, 0], [0, 0, 1])]
    try:
        _, _, vt = np.linalg.svd(mesh.vertices - mesh.vertices.mean(0), full_matrices=False)
        dirs += [vt[k] for k in range(3)]
    except np.linalg.LinAlgError:
        pass
    best = None
    lifted = pts + nrm * 0.02      # step off the surface: grazing rays on walls parallel to d must not self-hit
    for d in dirs:
        up = ~mesh.ray.intersects_any(lifted + d * 1e-3, np.tile(d, (len(pts), 1)))
        down = ~mesh.ray.intersects_any(lifted - d * 1e-3, np.tile(-d, (len(pts), 1)))
        free = up | down
        ratio = float(area_w[~free].sum() / area_w.sum())
        parallel = float((np.abs(nrm @ d) < math.sin(math.radians(0.5))).mean())
        row = dict(direction=np.round(d, 4).tolist(), undercut_ratio=round(ratio, 4), zero_draft_ratio=round(parallel, 4))
        if best is None or ratio < best["undercut_ratio"]:
            best = row
    return dict(best, samples=len(pts), method="ray visibility along +/-d, sampled")


def stability(parts: dict[str, trimesh.Trimesh]) -> dict:
    vols = np.array([max(m.volume, 0) for m in parts.values()])
    cents = np.array([m.center_mass for m in parts.values()])
    com = (cents * vols[:, None]).sum(0) / max(vols.sum(), 1e-9)
    allv = np.vstack([m.vertices for m in parts.values()])
    ymin = allv[:, 1].min()
    foot = allv[allv[:, 1] <= ymin + 0.6][:, [0, 2]]
    if len(foot) < 3:
        return dict(status="FAIL", reason="NO_SUPPORT_POINTS", center_of_mass=np.round(com, 3).tolist())
    try:
        hull = MultiPoint([tuple(p) for p in foot]).convex_hull
    except Exception:
        return dict(status="NOT_VERIFIED", reason="SUPPORT_POLYGON_FAILED")
    p = Point(com[0], com[2])
    inside = hull.area > 1e-6 and hull.contains(p)
    margin = float(p.distance(hull.exterior)) if hull.area > 1e-6 else 0.0
    return dict(status="PASS" if inside else "FAIL", center_of_mass=np.round(com, 3).tolist(),
                support_area_mm2=round(float(hull.area), 3), margin_mm=round(margin if inside else -margin, 3))


def legal_line(parts: dict[str, trimesh.Trimesh]) -> dict:
    allv = np.vstack([m.vertices for m in parts.values()])
    ymin = allv[:, 1].min()
    best = 0.0
    for m in parts.values():
        sel = (m.face_normals[:, 1] < -0.97) & (m.triangles_center[:, 1] <= ymin + 0.6)
        if sel.sum() < 2:
            continue
        pts = m.triangles[sel].reshape(-1, 3)[:, [0, 2]]
        try:
            rect = MultiPoint([tuple(p) for p in pts]).convex_hull.minimum_rotated_rectangle
            c = np.asarray(rect.exterior.coords)
            sides = np.linalg.norm(np.diff(c, axis=0), axis=1)
            best = max(best, float(sides.max()))
        except Exception:
            continue
    status = "PASS" if best >= 10 else ("WARN" if best >= 7 else "FAIL")
    return dict(status=status, longest_flat_bottom_mm=round(best, 3), target_mm="10-12 (min 7)")


def small_part(mesh: trimesh.Trimesh) -> dict:
    ext = np.sort(mesh.extents)
    fits = bool(ext[1] <= SMALL_PARTS_CYLINDER["diameter_mm"] and ext[2] <= SMALL_PARTS_CYLINDER["depth_mm"])
    return dict(fits_cylinder=fits, extents_mm=np.round(ext, 3).tolist(), **SMALL_PARTS_CYLINDER)


def check_parts(parts: dict[str, trimesh.Trimesh], material: dict, *, samples: int = 1500) -> dict:
    rows = []
    for name, m in parts.items():
        th = thickness(m, samples)
        uc = undercut(m, samples)
        row = dict(part=name, thickness={k: v for k, v in th.items() if not k.startswith("_")}, undercut=uc,
                   small_part=small_part(m))
        if th.get("status") == "NOT_VERIFIED":
            row["min_wall"] = "NOT_VERIFIED"
            row["sharp_points"] = "NOT_VERIFIED"
        else:
            row["min_wall"] = "PASS" if th["p05_mm"] >= material["min_wall_mm"] else "FAIL"
            # p.38: tips may locally reach ~1.02 mm; a region (not one grazing ray) thinner than that fails.
            clusters = thin_clusters(th["_points"], th["_values"], 1.02)
            row["thin_regions"] = clusters
            row["sharp_points"] = "FAIL" if clusters else "PASS"
            row["uniformity_ratio"] = round(th["p95_mm"] / max(th["p05_mm"], 1e-6), 3)
            if "max_solid_thickness_mm" in material:
                row["abs_max_thickness"] = "PASS" if th["p95_mm"] <= material["max_solid_thickness_mm"] else "FAIL"
        row["undercut_gate"] = "PASS" if uc["undercut_ratio"] <= material["max_undercut_area_ratio"] else "FAIL"
        rows.append(row)
    return dict(parts=rows, stability=stability(parts), legal_line=legal_line(parts))
