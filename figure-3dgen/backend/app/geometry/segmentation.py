"""Part-split analysis of the AI reference GLB.

The GLB partition is a *hypothesis* about how the figure is divided. For every pair of parts that
share a seam we measure geometric evidence; the SplitJudge model turns it into merge / resplit_clean /
keep proposals, and Astra (vision) confirms against the photo views. Nothing here changes geometry:
approved commands are executed by the Blender worker.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh
from scipy import sparse
from scipy.spatial import cKDTree

from .audit import boundary_loops, edge_counts


def _boundary_edges(mesh: trimesh.Trimesh):
    counts = edge_counts(mesh)
    mask = counts == 1
    edges = mesh.edges_unique[mask]
    # face adjacent to each unique boundary edge
    inv = mesh.edges_unique_inverse
    face_of_edge = np.full(len(mesh.edges_unique), -1)
    face_of_edge[inv] = np.repeat(np.arange(len(mesh.faces)), 3)
    faces = face_of_edge[mask]
    p0, p1 = mesh.vertices[edges[:, 0]], mesh.vertices[edges[:, 1]]
    return edges, (p0 + p1) / 2, np.linalg.norm(p1 - p0, axis=1), mesh.face_normals[faces]


def seam_jaggedness(mids: np.ndarray, lens: np.ndarray, samples: int = 600) -> float:
    """Loop-free, scale-aware jaggedness: median over seam points of (seam length inside a ball of
    radius R) / 2R, R = 4 x median edge length. A clean curve scores ~1.0-1.25 (triangle zig-zag);
    a noisy AI cut that wanders, branches or leaves islands scores much higher."""
    if len(mids) < 8:
        return 1.0
    radius = 4 * float(np.median(lens))
    tree = cKDTree(mids)
    pick = np.linspace(0, len(mids) - 1, min(samples, len(mids))).astype(int)
    ratios = [lens[tree.query_ball_point(mids[i], radius)].sum() / (2 * radius) for i in pick]
    return float(np.median(ratios))


def analyze(parts: dict[str, trimesh.Trimesh], height_mm: float) -> dict:
    tol = max(0.02, 0.0025 * height_mm)
    names = list(parts)
    total_area = sum(m.area for m in parts.values())
    data = {}
    for n in names:
        edges, mids, lens, nrm = _boundary_edges(parts[n])
        data[n] = dict(edges=edges, mids=mids, lens=lens, nrm=nrm,
                       boundary_mm=float(lens.sum()), tree=cKDTree(mids) if len(mids) else None)
    part_rows = []
    for n in names:
        m = parts[n]
        comps = trimesh.graph.connected_components(m.face_adjacency, nodes=np.arange(len(m.faces)), min_len=1)
        part_rows.append(dict(part=n, triangles=len(m.faces), area_mm2=round(float(m.area), 3),
                              area_share=round(float(m.area / max(total_area, 1e-9)), 5),
                              boundary_mm=round(data[n]["boundary_mm"], 3), shells=len(comps),
                              extent_mm=np.round(m.extents, 3).tolist()))
    seams = []
    for i, a in enumerate(names):
        da = data[a]
        if da["tree"] is None:
            continue
        for b in names[i + 1:]:
            db = data[b]
            if db["tree"] is None:
                continue
            if np.any(parts[a].bounds[0] - tol > parts[b].bounds[1]) or np.any(parts[b].bounds[0] - tol > parts[a].bounds[1]):
                continue
            dist, idx = db["tree"].query(da["mids"], distance_upper_bound=tol)
            hit = np.isfinite(dist)
            if hit.sum() < 3:
                continue
            seam_len = float(da["lens"][hit].sum())
            dist_b, _ = da["tree"].query(db["mids"], distance_upper_bound=tol)
            seam_len_b = float(db["lens"][np.isfinite(dist_b)].sum())
            cosang = np.abs((da["nrm"][hit] * db["nrm"][idx[hit]]).sum(1))
            dihedral = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
            jag = seam_jaggedness(da["mids"][hit], da["lens"][hit])
            small, large = sorted([a, b], key=lambda n: parts[n].area)
            seams.append(dict(
                id=f"S{len(seams):03d}", a=a, b=b,
                seam_mm=round(seam_len, 3),
                share_a=round(seam_len / max(da["boundary_mm"], 1e-9), 4),
                share_b=round(seam_len_b / max(db["boundary_mm"], 1e-9), 4),
                dihedral_median_deg=round(float(np.median(dihedral)), 3),
                dihedral_p90_deg=round(float(np.percentile(dihedral, 90)), 3),
                jaggedness=round(float(jag), 4),
                smaller=small, larger=large,
                area_ratio=round(float(parts[small].area / max(parts[large].area, 1e-9)), 5),
                smaller_area_share=round(float(parts[small].area / max(total_area, 1e-9)), 5),
                origin=np.round(da["mids"][hit].mean(0), 3).tolist(),
            ))
    return dict(tolerance_mm=tol, parts=part_rows, seams=seams)


# ---------------------------------------------------------------- split judge model
FEATURES = ("continuity", "enclosure", "fragment", "jagged", "share_min")


def features(seam: dict) -> np.ndarray:
    continuity = 1 - min(seam["dihedral_median_deg"], 60) / 60          # 1 = surface continues smoothly
    enclosure = max(seam["share_a"], seam["share_b"])                    # 1 = one part is a patch inside the other
    fragment = 1 - min(seam["smaller_area_share"] / 0.04, 1)             # 1 = tiny fragment
    jagged = float(np.clip((seam["jaggedness"] - 1.25) / 0.75, 0, 1))  # 1 = wandering cut
    share_min = min(seam["share_a"], seam["share_b"])
    return np.array([continuity, enclosure, fragment, jagged, share_min])


class SplitJudge:
    """Logistic model P(the seam wrongly cuts one continuous region) from seam features.

    PRIOR weights encode the rule of thumb (smooth continuation + enclosed patch + fragment => same
    region). `fit` retrains from human-approved decisions collected by the review UI.
    """

    PRIOR = dict(weights=[4.0, 3.0, 1.5, 0.8, 1.0], bias=-5.2, trained_on=0, source="prior (hand-set, untrained)")

    def __init__(self, params: dict | None = None):
        p = params or self.PRIOR
        self.w = np.asarray(p["weights"], float)
        self.b = float(p["bias"])
        self.meta = {k: v for k, v in p.items() if k not in ("weights", "bias")}

    def prob_merge(self, seam: dict) -> float:
        z = float(features(seam) @ self.w + self.b)
        return 1 / (1 + math.exp(-z))

    def decide(self, seam: dict, *, merge_at: float = 0.5, jagged_ratio: float = 1.5) -> dict:
        p = self.prob_merge(seam)
        if p >= merge_at:
            action = "merge"
        elif seam["jaggedness"] >= jagged_ratio and seam["seam_mm"] > 0:
            action = "resplit_clean"
        else:
            action = "keep"
        return dict(action=action, p_merge=round(p, 4), features=dict(zip(FEATURES, np.round(features(seam), 4).tolist())))

    def params(self) -> dict:
        return dict(weights=self.w.round(5).tolist(), bias=round(self.b, 5), **self.meta)

    @classmethod
    def fit(cls, seams: list[dict], labels: list[int], *, l2: float = 0.5, prior: dict | None = None) -> "SplitJudge":
        """MAP logistic regression centred on the prior (Newton). labels: 1 = should merge."""
        p0 = prior or cls.PRIOR
        w0 = np.r_[np.asarray(p0["weights"], float), p0["bias"]]
        X = np.column_stack([np.array([features(s) for s in seams]), np.ones(len(seams))])
        y = np.asarray(labels, float)
        w = w0.copy()
        for _ in range(50):
            pr = 1 / (1 + np.exp(-X @ w))
            grad = X.T @ (pr - y) + l2 * (w - w0)
            H = X.T @ (X * (pr * (1 - pr))[:, None]) + l2 * np.eye(len(w))
            step = np.linalg.solve(H, grad)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(dict(weights=w[:-1].tolist(), bias=float(w[-1]), trained_on=len(seams), source="MAP fit on approved decisions"))


# ---------------------------------------------------------------- clean re-split
def clean_resplit(a: trimesh.Trimesh, b: trimesh.Trimesh, *, iterations: int = 25, weld_mm: float = 1e-4):
    """Combine two parts, diffuse the face label field on the face graph and re-threshold.

    Removes zig-zag seams at the scale of several triangles while keeping each side's area within 20%.
    Returns (combined_mesh, labels) where label 0 = a, 1 = b.
    """
    combined = trimesh.util.concatenate([a, b])
    combined.merge_vertices(digits_vertex=max(1, int(-math.log10(weld_mm))))
    labels0 = np.r_[np.zeros(len(a.faces)), np.ones(len(b.faces))]
    adj = combined.face_adjacency
    n = len(combined.faces)
    W = sparse.coo_matrix((np.ones(len(adj) * 2), (np.r_[adj[:, 0], adj[:, 1]], np.r_[adj[:, 1], adj[:, 0]])), shape=(n, n)).tocsr()
    deg = np.asarray(W.sum(1)).ravel()
    field = labels0.copy()
    for _ in range(iterations):
        field = 0.5 * field + 0.5 * (W @ field) / np.maximum(deg, 1)
    area = combined.area_faces
    target = (labels0 * area).sum()
    # threshold chosen so that side b keeps its original area (monotone in the threshold)
    order = np.argsort(-field)
    cum = np.cumsum(area[order])
    k = int(np.searchsorted(cum, target))
    labels = np.zeros(n, int)
    labels[order[:k + 1]] = 1
    # islands -> majority neighbour label
    for lab in (0, 1):
        sel = np.flatnonzero(labels == lab)
        sub = W[sel][:, sel]
        ncomp, comp = sparse.csgraph.connected_components(sub, directed=False)
        if ncomp > 1:
            sizes = np.bincount(comp, weights=area[sel])
            keep = np.argmax(sizes)
            labels[sel[comp != keep]] = 1 - lab
    changed = float(area[labels != labels0].sum() / area.sum())
    return combined, labels, dict(changed_area_ratio=round(changed, 4), iterations=iterations)
