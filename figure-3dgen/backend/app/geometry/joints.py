"""Assembly graph, joint planning and integrated male/female construction (manifold3d kernel).

Ported from the 2026-09-27 keyed builder (master-profile pin + clearance cutter + boss envelope),
reorganised around an explicit assembly graph with one plan per interface.
"""
from __future__ import annotations

import math
from collections import defaultdict

import manifold3d as md
import numpy as np
import shapely
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point, Polygon

EPS_VOL = 1e-6


def solid(mesh: trimesh.Trimesh) -> md.Manifold:
    m = md.Manifold(md.Mesh64(np.array(mesh.vertices, dtype=np.float64, order="C"), np.array(mesh.faces, dtype=np.uint64, order="C")))
    if m.status() != md.Error.NoError or not math.isfinite(m.volume()) or m.volume() <= 0:
        raise ValueError("INVALID_CLOSED_SOLID")
    return m


def mesh_of(m: md.Manifold) -> trimesh.Trimesh:
    d = m.to_mesh64()
    return trimesh.Trimesh(np.asarray(d.vert_properties)[:, :3], np.asarray(d.tri_verts), process=False)


def vol(m: md.Manifold) -> float:
    v = m.volume()
    if m.status() != md.Error.NoError or not math.isfinite(v):
        raise ValueError("BOOLEAN_KERNEL_FAILURE")
    return max(0.0, float(v))


# ------------------------------------------------------------------ partition
def offset_solid(mesh: trimesh.Trimesh, distance: float) -> md.Manifold:
    """Approximate outward offset (vertex normals). Falls back to the exact solid if invalid."""
    if distance <= 0:
        return solid(mesh)
    try:
        moved = trimesh.Trimesh(np.asarray(mesh.vertices) + mesh.vertex_normals * distance, np.asarray(mesh.faces), process=False)
        grown = solid(moved)
        if vol(grown) >= vol(solid(mesh)):
            return grown
    except ValueError:
        pass
    return solid(mesh)


def resolve_overlaps(parts: dict[str, trimesh.Trimesh], clearance_mm: float = 0.1) -> tuple[dict[str, trimesh.Trimesh], list[dict]]:
    """Interpenetrating editable parts -> disjoint moldable solids. The larger body is carved by the
    smaller one grown by `clearance_mm` (e.g. torso receives the neck volume with a gap), so the smaller
    keeps its sculpted shape and parallel contact faces do not rub during assembly."""
    solids = {n: solid(m) for n, m in parts.items()}
    names = sorted(solids, key=lambda n: vol(solids[n]), reverse=True)
    rows = []
    for i, big in enumerate(names):
        for small in names[i + 1:]:
            overlap = vol(solids[big] ^ solids[small])
            if overlap <= EPS_VOL:
                continue
            before = vol(solids[big])
            cut = solids[big] - offset_solid(mesh_of(solids[small]), clearance_mm)
            pieces = sorted(cut.decompose(), key=vol, reverse=True)
            main = pieces[0] if pieces else None
            slivers = sum(vol(p) for p in pieces[1:])
            # thin shells (capes) can cut off sub-millimetre slivers; anything larger is a redesign case
            if main is None or vol(main) < before * 0.6 or slivers > max(0.005 * before, 1.0):
                raise ValueError(f"PARTITION_REDESIGN_REQUIRED:{big}:{small}")
            solids[big] = main
            rows.append(dict(carved=big, by=small, removed_mm3=round(overlap, 4), dropped_slivers_mm3=round(slivers, 4),
                             contact_clearance_mm=clearance_mm))
    return {n: mesh_of(s) for n, s in solids.items()}, rows


def apply_merges(parts: dict[str, trimesh.Trimesh], merges: list[dict]) -> tuple[dict[str, trimesh.Trimesh], dict[str, str], list[dict]]:
    """Union merge groups on the ORIGINAL (touching / overlapping) solids. Returns parts, name map, rows."""
    parent = {n: n for n in parts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for m in merges:
        a, b = find(m["parts"][0]), find(m["parts"][1])
        if a == b:
            continue
        keep, drop = (a, b) if parts[a].volume >= parts[b].volume else (b, a)
        parent[drop] = keep
    groups: dict[str, list[str]] = {}
    for n in parts:
        groups.setdefault(find(n), []).append(n)
    out, rows = {}, []
    for keep, members in groups.items():
        s = solid(parts[keep])
        for other in members:
            if other != keep:
                s = s + solid(parts[other])
        pieces = s.decompose()
        if len(pieces) != 1:
            raise ValueError(f"MERGE_NOT_CONNECTED:{sorted(members)}")
        out[keep] = mesh_of(s) if len(members) > 1 else parts[keep]
        if len(members) > 1:
            rows.append(dict(into=keep, members=sorted(members)))
    return out, {n: find(n) for n in parts}, rows


# ------------------------------------------------------------------ contacts
def contact_graph(parts: dict[str, trimesh.Trimesh], *, tol_mm: float = 0.25, samples: int = 6000,
                  min_area_mm2: float = 1.0) -> list[dict]:
    """Interfaces = clusters of surface points of A lying within tol of B. One pair can have several."""
    names = list(parts)
    pq = {n: trimesh.proximity.ProximityQuery(parts[n]) for n in names}
    out = []
    for i, a in enumerate(names):
        A = parts[a]
        for b in names[i + 1:]:
            B = parts[b]
            if np.any(A.bounds[0] - tol_mm > B.bounds[1]) or np.any(B.bounds[0] - tol_mm > A.bounds[1]):
                continue
            n = int(np.clip(samples * A.area / 2000, 800, samples))
            pts, fid = trimesh.sample.sample_surface_even(A, n, seed=11) if hasattr(trimesh.sample, "sample_surface_even") else trimesh.sample.sample_surface(A, n)
            lo, hi = B.bounds[0] - tol_mm, B.bounds[1] + tol_mm
            near = np.all((pts >= lo) & (pts <= hi), axis=1)
            if near.sum() < 5:
                continue
            sd = pq[b].signed_distance(pts[near])        # >0 inside B
            hit = np.abs(sd) <= tol_mm
            if hit.sum() < 5:
                continue
            cpts = pts[near][hit]
            spacing = math.sqrt(A.area / max(len(pts), 1)) * 2.5
            tree = cKDTree(cpts)
            pairs = tree.query_pairs(spacing, output_type="ndarray")
            g = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(cpts), len(cpts))) if len(pairs) else coo_matrix((len(cpts), len(cpts)))
            k, lab = connected_components(g, directed=False)
            area_per = A.area / max(len(pts), 1)
            for c in range(k):
                p = cpts[lab == c]
                area = len(p) * area_per
                if area < min_area_mm2 or len(p) < 5:
                    continue
                out.append(describe_interface(f"I{len(out):03d}", a, b, p, area, parts))
    return out


def describe_interface(iid: str, a: str, b: str, pts: np.ndarray, area: float, parts) -> dict:
    center = pts.mean(0)
    _, s, vt = np.linalg.svd(pts - center, full_matrices=False)
    normal = vt[2] if len(s) > 2 else np.array([0, 1.0, 0])
    # orient a -> b
    if np.dot(parts[b].centroid - parts[a].centroid, normal) < 0:
        normal = -normal
    xy = (pts - center) @ vt[:2].T
    hull = MultiPoint([tuple(q) for q in xy]).convex_hull
    if isinstance(hull, Polygon) and hull.area > 1e-6:
        rep = hull.representative_point() if not hull.contains(Point(0, 0)) else Point(0, 0)
        room = float(rep.distance(hull.exterior))
        origin = center + rep.x * vt[0] + rep.y * vt[1]
        corners = np.asarray(hull.minimum_rotated_rectangle.exterior.coords)
        width = float(np.linalg.norm(np.diff(corners, axis=0), axis=1).min())
    else:
        room, origin, width = 0.0, center, 0.0
    return dict(id=iid, a=a, b=b, origin=np.round(origin, 4).tolist(), normal_ab=np.round(normal, 5).tolist(),
                area_mm2=round(float(area), 3), room_radius_mm=round(room, 3), diameter_mm=round(2 * room, 3),
                planarity_rms_mm=round(float(s[2] / math.sqrt(len(pts))) if len(s) > 2 else 0.0, 4),
                min_width_mm=round(width, 3), status="detected")


# ------------------------------------------------------------------ planning
def size_class(diameter: float, classes: list[dict]) -> dict:
    for c in classes:
        if diameter <= c["max_interface_mm"]:
            return c
    return classes[-1]


def plan_joints(parts: dict[str, trimesh.Trimesh], interfaces: list[dict], defaults: dict,
                library: dict | None = None) -> list[dict]:
    """One plan per interface. Female = larger body (more material around the socket) unless the
    S3-derived library says otherwise. Joints of the same pair share one insertion axis."""
    by_pair = defaultdict(list)
    for it in interfaces:
        va, vb = parts[it["a"]].volume, parts[it["b"]].volume
        male, female = (it["a"], it["b"]) if va <= vb else (it["b"], it["a"])
        axis = np.asarray(it["normal_ab"]) * (1 if male == it["a"] else -1)   # male -> female
        by_pair[(male, female)].append((it, axis))
    plans = []
    for (male, female), group in by_pair.items():
        axis = sum(ax * max(it["area_mm2"], 0.1) for it, ax in group)
        axis = axis / max(np.linalg.norm(axis), 1e-9)
        for it, _ in group:
            cls = size_class(it["diameter_mm"], defaults["size_classes"])
            gap, wall = defaults["radial_clearance_mm"], defaults["socket_wall_mm"]
            radius = min(cls["pin_radius_mm"], max(0.3, it["room_radius_mm"] - gap - wall))
            archetype = "d_key_pin" if len(group) == 1 else "round_pin"
            if it["diameter_mm"] >= 16 and len(group) == 1:
                archetype = "neck_plug"
            source = "assumption_defaults"
            if library and library.get("archetypes"):
                ref = retrieve(library, it["diameter_mm"])
                if ref:
                    archetype = {"keyed_pin": "d_key_pin"}.get(ref["archetype"], ref["archetype"])
                    # observed reference values, bounded so one odd sample cannot produce nonsense
                    if ref.get("clearance_median_mm") is not None:
                        gap = float(np.clip(ref["clearance_median_mm"], 0.05, 0.4))
                    if ref.get("socket_wall_p05_mm"):
                        wall = float(np.clip(ref["socket_wall_p05_mm"], 0.8, 3.0))
                    radius = min(radius, max(0.3, it["room_radius_mm"] - gap - wall))
                    source = f"s3_library:{ref.get('sample_ids', [])[:3]}"
            plans.append(dict(interface_id=it["id"], male=male, female=female, origin=it["origin"],
                              axis=np.round(axis, 6).tolist(), archetype=archetype,
                              profile={"d_key_pin": "d_key", "neck_plug": "d_key", "round_pin": "round"}.get(archetype, "round"),
                              size_class=cls["class"], radius_mm=round(float(radius), 3),
                              length_mm=cls["pin_length_mm"] if archetype != "neck_plug" else cls["pin_length_mm"] * 0.8,
                              clearance_mm=gap, end_clearance_mm=defaults["end_clearance_mm"], wall_mm=wall,
                              parameter_source=source, status="proposed"))
    return plans


def retrieve(library: dict, diameter: float) -> dict | None:
    rows = [r for r in library.get("archetypes", []) if r.get("interface_diameter_mm")]
    if not rows:
        return None
    return min(rows, key=lambda r: abs(math.log(max(r["interface_diameter_mm"], 0.1) / max(diameter, 0.1))))


# ------------------------------------------------------------------ construction
def master_key(origin, axis, radius, start, end, profile="round", clearance=0.0) -> trimesh.Trimesh:
    ang = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    poly = Polygon(np.column_stack([radius * np.cos(ang), radius * np.sin(ang)]))
    if profile == "d_key":
        poly = poly.intersection(shapely.box(-radius * 2, -radius * 2, radius * 0.6, radius * 2))
    if clearance:
        poly = poly.buffer(clearance, quad_segs=8)   # true 2D offset: the flat also gets clearance
    mesh = trimesh.creation.extrude_polygon(poly, end - start)
    mesh.apply_translation([0, 0, start])
    T = trimesh.geometry.align_vectors([0, 0, 1], np.asarray(axis, float))
    T[:3, 3] = origin
    mesh.apply_transform(T)
    return mesh


def construct(solids: dict[str, md.Manifold], plan: dict) -> tuple[md.Manifold, md.Manifold, dict]:
    ma, fe = solids[plan["male"]], solids[plan["female"]]
    o, ax = np.asarray(plan["origin"], float), np.asarray(plan["axis"], float)
    r, L, gap, wall, endgap = (plan[k] for k in ("radius_mm", "length_mm", "clearance_mm", "wall_mm", "end_clearance_mm"))
    root = plan.get("root_mm", max(1.5, r * 1.5))
    pin = solid(master_key(o, ax, r, -root, L, plan["profile"]))
    cutter = solid(master_key(o, ax, r, -root - 0.25, L + endgap, plan["profile"], gap))
    envelope = solid(master_key(o, ax, r, 0.3, L + endgap + wall, plan["profile"], gap + wall))
    if vol(ma ^ pin) < 0.015 or vol(pin - ma) < 0.015:
        raise ValueError("PIN_NOT_ROOTED_OR_NOT_PROTRUDING")
    if vol(fe ^ envelope) < 0.03:
        raise ValueError("SOCKET_BOSS_NOT_ROOTED")
    # The socket wall must already exist inside the female body: a boss may only fill a small gap on a
    # curved interface. Growing a large boss outside the sculpted surface would silently change the design.
    outside_boss = vol(envelope - fe)
    if outside_boss > 0.1 * vol(envelope):
        raise ValueError("INSUFFICIENT_WALL_REDESIGN_REQUIRED")
    if vol((pin - ma) - (fe + envelope)) > 0.02 * vol(pin):
        raise ValueError("PIN_EXPOSED_OUTSIDE_BODIES")
    nm = ma + pin
    receiver = fe + envelope
    nf = receiver - cutter
    if len(nm.decompose()) != 1 or len(nf.decompose()) != 1:
        raise ValueError("DISCONNECTED_KEY_OR_BODY")
    if vol(nm ^ nf) > EPS_VOL:
        raise ValueError("KEY_BODY_INTERFERENCE")
    if vol(receiver - nf) < 0.015 or vol(pin ^ nf) > EPS_VOL:
        raise ValueError("SOCKET_NOT_CUT")
    if vol((envelope - cutter) - nf) > EPS_VOL:
        raise ValueError("SOCKET_WALL_OR_FLOOR_MISSING")
    added_boss = vol(receiver - fe)
    return nm, nf, dict(pin_integrated=True, socket_cut=True, root_overlap_mm3=round(vol(ma ^ pin), 4),
                        protruding_mm3=round(vol(pin - ma), 4), socket_removed_mm3=round(vol(receiver - nf), 4),
                        boss_added_mm3=round(added_boss, 4), boss_visible=added_boss > 0.05)


def build_all(parts: dict[str, trimesh.Trimesh], plans: list[dict], on_row=None) -> tuple[dict | None, dict]:
    solids = {n: solid(m) for n, m in parts.items()}
    rows = []
    for idx, original in enumerate(plans):
        errors = []
        for offset in (0.0, 0.5, -0.5, 1.0, -1.0, 2.0):
            for shrink in (1.0, 0.8, 0.65):
                plan = dict(original, radius_mm=round(original["radius_mm"] * shrink, 3),
                            origin=(np.asarray(original["origin"]) + np.asarray(original["axis"]) * offset).round(5).tolist())
                try:
                    m, f, ev = construct(solids, plan)
                    solids[plan["male"]], solids[plan["female"]] = m, f
                    rows.append(dict(interface_id=plan["interface_id"], status="built", plan=plan, evidence=ev,
                                     adjusted=dict(offset_mm=offset, radius_scale=shrink)))
                    break
                except ValueError as exc:
                    errors.append(f"offset {offset} x{shrink}: {exc}")
            else:
                continue
            break
        else:
            rows.append(dict(interface_id=original["interface_id"], status="blocked", plan=original, reasons=errors[-6:]))
        if on_row:
            on_row(idx + 1, len(plans), rows[-1])
    report = dict(joints=rows, required=len(plans), built=sum(r["status"] == "built" for r in rows))
    if report["built"] != report["required"]:
        return None, dict(report, status="BLOCKED")
    # later joints must not have destroyed earlier pins / walls
    for row in rows:
        p = row["plan"]
        root = p.get("root_mm", max(1.5, p["radius_mm"] * 1.5))
        pin = solid(master_key(p["origin"], p["axis"], p["radius_mm"], -root, p["length_mm"], p["profile"]))
        cutter = solid(master_key(p["origin"], p["axis"], p["radius_mm"], -root - 0.25, p["length_mm"] + p["end_clearance_mm"], p["profile"], p["clearance_mm"]))
        if vol(pin - solids[p["male"]]) > EPS_VOL or vol(cutter ^ solids[p["female"]]) > EPS_VOL:
            row.update(status="blocked", reasons=["LATER_JOINT_REMOVED_PIN_OR_SOCKET"])
    overlaps = []
    names = list(solids)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = vol(solids[a] ^ solids[b])
            if v > EPS_VOL:
                overlaps.append(dict(a=a, b=b, overlap_mm3=round(v, 6)))
    report["interference"] = overlaps
    if overlaps or any(r["status"] != "built" for r in rows):
        return None, dict(report, status="BLOCKED")
    return {n: mesh_of(s) for n, s in solids.items()}, dict(report, status="BUILT")
