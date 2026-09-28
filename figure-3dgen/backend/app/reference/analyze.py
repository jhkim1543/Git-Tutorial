"""Ground-truth CAD analysis: detect male/female joints in an ASSEMBLED multi-part reference model.

Input: the parts of one reference figure exported from FreeForm (.cly is not readable here) as
STL / PLY / OBJ / GLB — one file per part, or one file whose disconnected bodies are the parts.
Every result is `observed` (measured on geometry); nothing is inferred from file names.

Detection: sample the surface of part A near part B. A point is *enclosed* when rays in many
directions hit B within a short range — a pin sitting in a socket. Clusters of enclosed points are
joints with A = male, B = female. Escaping ray directions give the withdrawal axis; the cross-section
gives the profile (round vs keyed); the A->B distance gives the clearance; rays into B give the wall.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

MESH_EXT = {".stl", ".ply", ".obj", ".glb", ".gltf", ".off"}


def sphere_dirs(n: int = 26) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


def load_assembly(path: Path, unit_scale: float = 1.0) -> dict[str, trimesh.Trimesh]:
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in MESH_EXT) if path.is_dir() else [path]
    parts: dict[str, trimesh.Trimesh] = {}
    for f in files:
        loaded = trimesh.load(f, force="scene", process=False)
        geoms = [g for g in loaded.dump() if isinstance(g, trimesh.Trimesh) and len(g.faces)]
        mesh = trimesh.util.concatenate(geoms)
        mesh.merge_vertices()
        bodies = mesh.split(only_watertight=False) if len(files) == 1 else [mesh]
        for k, body in enumerate(bodies):
            body.apply_scale(unit_scale)
            parts[f"{f.stem}" + (f"#{k}" if len(bodies) > 1 else "")] = body
    return parts


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float, float]:
    A = np.column_stack([2 * xy, np.ones(len(xy))])
    b = (xy ** 2).sum(1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    c = sol[:2]
    r = math.sqrt(max(sol[2] + (c ** 2).sum(), 1e-12))
    resid = float(np.sqrt(np.mean((np.linalg.norm(xy - c, axis=1) - r) ** 2)))
    return c, r, resid


def analyze_pair(a: str, b: str, A: trimesh.Trimesh, B: trimesh.Trimesh, *, gap_max: float = 0.8,
                 reach: float = 6.0, samples: int = 12000, enclosed_at: float = 0.6) -> list[dict]:
    lo, hi = B.bounds[0] - gap_max, B.bounds[1] + gap_max
    n = int(np.clip(samples * A.area / 3000, 2000, samples))
    pts, fid = trimesh.sample.sample_surface(A, n, seed=2)
    near = np.all((pts >= lo) & (pts <= hi), axis=1)
    if near.sum() < 10:
        return []
    pq = trimesh.proximity.ProximityQuery(B)
    sd = pq.signed_distance(pts[near])            # > 0 inside B
    close = (sd <= 0.05) & (sd >= -gap_max)
    if close.sum() < 10:
        return []
    cp, cn, gap = pts[near][close], A.face_normals[fid[near][close]], -sd[close]
    dirs = sphere_dirs()
    k = len(cp)
    origins = np.repeat(cp, len(dirs), axis=0)
    rays = np.tile(dirs, (k, 1))
    locs, idx, _ = B.ray.intersects_location(origins + rays * 1e-4, rays, multiple_hits=False)
    hit = np.zeros(k * len(dirs), bool)
    dist = np.linalg.norm(locs - origins[idx], axis=1)
    hit[idx[dist <= reach]] = True
    hit = hit.reshape(k, len(dirs))
    enclosure = hit.mean(1)
    male = enclosure >= enclosed_at
    out = []
    if male.sum() >= 12:
        mp = cp[male]
        spacing = math.sqrt(A.area / n) * 3
        pairs = cKDTree(mp).query_pairs(spacing, output_type="ndarray")
        g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(mp), len(mp))) if len(pairs) else coo_matrix((len(mp), len(mp)))
        ncomp, lab = connected_components(g, directed=False)
        male_idx = np.flatnonzero(male)
        for c in range(ncomp):
            sel = male_idx[lab == c]
            if len(sel) < 12:
                continue
            row = describe_joint(a, b, cp[sel], cn[sel], gap[sel], hit[sel], dirs, B)
            if row is not None:
                out.append(row)
    face = (~male) & (enclosure < 0.4) & (gap < 0.2)
    if face.sum() >= 30:
        area = face.sum() * A.area / n
        out.append(dict(kind="face_contact", male=None, female=None, a=a, b=b, contact_area_mm2=round(float(area), 3),
                        status="observed", note="flat/butt contact without an enclosing socket (glue face or seam)"))
    return out


def describe_joint(a, b, pts, nrm, gap, hits, dirs, B) -> dict:
    escape = (~hits).astype(float) @ dirs
    axis = escape.sum(0)
    axis = axis / max(np.linalg.norm(axis), 1e-9)          # withdrawal direction of the male
    c = pts.mean(0)
    u = np.cross(axis, [1, 0, 0] if abs(axis[0]) < 0.9 else [0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    rel = pts - c
    side = np.abs(nrm @ axis) < 0.5                         # lateral (wall) samples only for the profile
    radial = rel - np.outer(along_axis := rel @ axis, axis)
    radial_n = radial / np.maximum(np.linalg.norm(radial, axis=1, keepdims=True), 1e-9)
    convexity = float(np.mean(np.sign((nrm * radial_n).sum(1))[side])) if side.any() else 0.0
    if convexity < 0.3:
        return None     # concave = this is the socket wall of A around B's pin, not a male feature
    xy = np.column_stack([rel @ u, rel @ v])[side] if side.sum() >= 8 else np.column_stack([rel @ u, rel @ v])
    center, r, resid = fit_circle(xy)
    length = float(along_axis.max() - along_axis.min())
    roundness = resid / max(r, 1e-9)
    ext = np.ptp(xy, axis=0)
    if r * 2 >= 12 and length < r * 2:
        archetype = "neck_plug"
    elif roundness < 0.06:
        archetype = "round_pin"
    else:
        archetype = "keyed_pin"
    # female wall: from the socket wall points (closest on B) cast rays into B's material
    pq = trimesh.proximity.ProximityQuery(B)
    wall_pts, _, tri = pq.on_surface(pts[side][:200] if side.sum() else pts[:200])
    inward = -B.face_normals[tri]
    locs, idx, _ = B.ray.intersects_location(wall_pts + inward * 1e-3, inward, multiple_hits=False)
    wall = np.linalg.norm(locs - wall_pts[idx], axis=1) if len(idx) else np.array([])
    return dict(kind="pin_socket", archetype=archetype, male=a, female=b, status="observed",
                origin=np.round(c, 3).tolist(), withdraw_axis=np.round(axis, 4).tolist(),
                interface_diameter_mm=round(2 * r, 3), pin_radius_mm=round(r, 3), pin_length_mm=round(length, 3),
                profile_roundness=round(roundness, 4), profile_extent_mm=np.round(ext, 3).tolist(), convexity=round(convexity, 3),
                clearance_median_mm=round(float(np.median(gap[side])) if side.any() else float(np.median(gap)), 4),
                clearance_p90_mm=round(float(np.percentile(gap, 90)), 4),
                socket_wall_p05_mm=round(float(np.percentile(wall, 5)), 3) if len(wall) else None,
                samples=int(len(pts)))


def analyze_assembly(parts: dict[str, trimesh.Trimesh], **kw) -> dict:
    names = list(parts)
    joints = []
    for i, a in enumerate(names):
        for b in names:
            if a == b:
                continue
            A, B = parts[a], parts[b]
            if np.any(A.bounds[0] - 1 > B.bounds[1]) or np.any(B.bounds[0] - 1 > A.bounds[1]):
                continue
            rows = analyze_pair(a, b, A, B, **kw)
            for r in rows:
                if r["kind"] == "face_contact" and any(j["kind"] == "face_contact" and {j["a"], j["b"]} == {a, b} for j in joints):
                    continue
                joints.append(r)
    info = [dict(part=n, triangles=len(m.faces), watertight=bool(m.is_watertight),
                 volume_mm3=round(float(m.volume), 3) if m.is_watertight else None,
                 extents_mm=np.round(m.extents, 3).tolist()) for n, m in parts.items()]
    pins = [j for j in joints if j["kind"] == "pin_socket"]
    for j in pins:
        vm, vf = parts[j["male"]].volume, parts[j["female"]].volume
        j["male_is_smaller_part"] = bool(vm < vf)
    return dict(parts=info, part_count=len(info), joints=joints, pin_socket_count=len(pins),
                face_contact_count=sum(j["kind"] == "face_contact" for j in joints))


def build_library(results: dict[str, dict], min_samples: int = 10) -> dict:
    """Aggregate analysed samples into the joint library used by the Moldable planner."""
    rows = []
    for sid, res in results.items():
        for j in res.get("joints", []):
            if j["kind"] == "pin_socket":
                rows.append(dict(j, sample_ids=[sid]))
    status = "READY" if len({r["sample_ids"][0] for r in rows}) >= min_samples else "PARTIAL" if rows else "EMPTY"
    by = {}
    for r in rows:
        by.setdefault(r["archetype"], []).append(r)
    summary = {k: dict(count=len(v), interface_diameter_mm=_q([x["interface_diameter_mm"] for x in v]),
                       clearance_median_mm=_q([x["clearance_median_mm"] for x in v]),
                       socket_wall_p05_mm=_q([x["socket_wall_p05_mm"] for x in v if x["socket_wall_p05_mm"] is not None]),
                       male_is_smaller_ratio=round(float(np.mean([x["male_is_smaller_part"] for x in v])), 3))
               for k, v in by.items()}
    keep = ("archetype", "interface_diameter_mm", "pin_radius_mm", "pin_length_mm", "clearance_median_mm",
            "socket_wall_p05_mm", "male_is_smaller_part", "sample_ids")
    return dict(status=status, samples=len(results), archetypes=[{k: r[k] for k in keep} for r in rows], summary=summary,
                note="observed on exported reference CAD; not a manufacturing tolerance approval")


def _q(values):
    if not values:
        return None
    a = np.asarray(values, float)
    return dict(p10=round(float(np.percentile(a, 10)), 3), p50=round(float(np.median(a)), 3), p90=round(float(np.percentile(a, 90)), 3))
