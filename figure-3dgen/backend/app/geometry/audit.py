"""Mesh integrity audit (position-welded) and boundary loop extraction."""
from __future__ import annotations

import numpy as np
import trimesh


def edge_counts(mesh: trimesh.Trimesh) -> np.ndarray:
    return np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))


def audit(mesh: trimesh.Trimesh) -> dict:
    """Closed = no open / non-manifold edges, no degenerate faces, consistent winding, positive volume."""
    if not len(mesh.faces):
        return dict(triangles=0, closed=False, open_edges=0, nonmanifold_edges=0, degenerate_faces=0,
                    winding_consistent=False, volume_mm3=0.0, shells=0, area_mm2=0.0)
    counts = edge_counts(mesh)
    open_edges = int((counts == 1).sum())
    nonmanifold = int((counts > 2).sum())
    degenerate = int((mesh.area_faces < 1e-10).sum())
    winding = bool(mesh.is_winding_consistent) if not nonmanifold else False
    volume = float(mesh.volume) if winding else 0.0
    shells = int(trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(mesh.faces)),
                                                    min_len=1).__len__())
    closed = bool(open_edges == 0 and nonmanifold == 0 and degenerate == 0 and winding and volume > 0)
    return dict(triangles=len(mesh.faces), vertices=len(mesh.vertices), open_edges=open_edges,
                nonmanifold_edges=nonmanifold, degenerate_faces=degenerate, winding_consistent=winding,
                volume_mm3=round(volume, 4), shells=shells, area_mm2=round(float(mesh.area), 4),
                bounds_mm=np.round(mesh.bounds, 4).tolist(), closed=closed)


def boundary_loops(mesh: trimesh.Trimesh) -> list[dict]:
    """Open-edge loops. Branched boundaries (vertex degree != 2) are reported as not simple."""
    counts = edge_counts(mesh)
    edges = mesh.edges_unique[counts == 1]
    adj: dict[int, set[int]] = {}
    for a, b in edges:
        adj.setdefault(int(a), set()).add(int(b))
        adj.setdefault(int(b), set()).add(int(a))
    remaining, loops = set(adj), []
    while remaining:
        seed = min(remaining)
        component, todo = set(), [seed]
        while todo:
            v = todo.pop()
            if v not in component:
                component.add(v)
                todo.extend(adj[v] - component)
        remaining -= component
        if any(len(adj[v]) != 2 for v in component):
            loops.append(dict(simple=False, ids=sorted(component)))
            continue
        ids, prev, v = [seed], None, seed
        while True:
            nxt = min(adj[v] - ({prev} if prev is not None else set()))
            if nxt == seed:
                break
            ids.append(nxt)
            prev, v = v, nxt
        loops.append(dict(simple=True, ids=ids))
    return loops


def loop_length(points: np.ndarray, closed: bool = True) -> float:
    seg = np.diff(np.vstack([points, points[:1]]) if closed else points, axis=0)
    return float(np.linalg.norm(seg, axis=1).sum())


def jaggedness(points: np.ndarray, iterations: int = 12) -> float:
    """Polyline length / length after Laplacian smoothing (>= 1). Zig-zag triangle-edge seams score high."""
    if len(points) < 6:
        return 1.0
    p = points.astype(float).copy()
    for _ in range(iterations):
        p = 0.5 * p + 0.25 * (np.roll(p, 1, axis=0) + np.roll(p, -1, axis=0))
    smooth = loop_length(p)
    return float(loop_length(points) / smooth) if smooth > 1e-9 else 1.0
