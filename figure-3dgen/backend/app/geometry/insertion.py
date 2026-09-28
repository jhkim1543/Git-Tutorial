"""Full-interval straight insertion sweep (ported from the 2026-09-27 handoff).

Every triangle of the moving part sweeps a prism along the displacement; any positive-volume
intersection with a stationary part is a collision. Not a minimum-clearance or rotation proof.
"""
from __future__ import annotations

import time

import manifold3d as md
import numpy as np
import trimesh

from .joints import solid, vol

PRISM = np.array([[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5]], dtype=np.uint64)


def sweep(moving: trimesh.Trimesh, stationary: list[trimesh.Trimesh], displacement, *, seconds: float = 90) -> dict:
    delta = np.asarray(displacement, float)
    if delta.shape != (3,) or np.linalg.norm(delta) < 1e-8:
        raise ValueError("INVALID_DISPLACEMENT")
    started = time.monotonic()
    obstacles = [solid(m) for m in stationary]
    body = solid(moving)
    initial = [vol(body ^ o) for o in obstacles]
    bounds = [m.bounds for m in stationary]
    total, tested, hits = np.zeros(len(obstacles)), 0, []
    for i, tri in enumerate(moving.triangles):
        if time.monotonic() - started > seconds:
            return dict(status="NOT_VERIFIED", reason="TIME_BUDGET", tested_prisms=tested)
        lo = np.minimum(tri.min(0), (tri + delta).min(0))
        hi = np.maximum(tri.max(0), (tri + delta).max(0))
        ids = [k for k, b in enumerate(bounds) if np.all(hi >= b[0]) and np.all(lo <= b[1])]
        if not ids:
            continue
        signed = float(np.dot(np.cross(tri[1] - tri[0], tri[2] - tri[0]), delta))
        if abs(signed) < 1e-12:
            continue
        faces = PRISM if signed > 0 else PRISM[:, ::-1]
        prism = md.Manifold(md.Mesh64(np.ascontiguousarray(np.vstack([tri, tri + delta])), np.ascontiguousarray(faces)))
        tested += 1
        for k in ids:
            v = vol(prism ^ obstacles[k])
            total[k] += v
            if v > 1e-7 and len(hits) < 12:
                hits.append(dict(triangle=i, obstacle=k, overlap_mm3=round(v, 6)))
    ok = max(initial, default=0) <= 1e-7 and total.max(initial=0) <= 1e-7
    return dict(status="PASS" if ok else "FAIL", displacement_mm=np.round(delta, 4).tolist(), tested_prisms=tested,
                initial_overlap_mm3=[round(x, 6) for x in initial], swept_overlap_mm3=np.round(total, 6).tolist(),
                hits=hits, seconds=round(time.monotonic() - started, 2),
                method="per-triangle straight translation prisms (positive-volume screen)")
