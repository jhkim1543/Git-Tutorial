"""Blender command interpreter. Runs inside Blender (or Python with the `bpy` module).

Input: <job>/plan.json = {"ops": [...]} — a closed set of JSON operations, never code.
Output: <job>/result.json, per-op records; <job>/progress.json while running.
Public geometry is Y-up mm; Blender native is Z-up mm: (x, y, z)pub -> (x, -z, y)bl.

Ops
  load        {name, file}                         npz(vertices, faces) -> object
  join        {parts:[...], into}                  semantic merge
  separate    {source, labels, names:[...]}        face-label split (labels npz 'labels')
  append      {blend, objects:[...]}                 objects from a per-part .blend
  replace     {old:[...], new:[...]}                delete old objects, rename new ones to the old names
  rebuild     {part, mode, voxel_mm, shell_mm, target_quads, smooth_iterations}
              reference (possibly open) -> NEW closed quad-dominant solid
  save_npz    {part, file}                          triangulated export for Python checks
  export      {blend, obj}                          whole scene
  roundtrip   {obj}                                 OBJ re-import: names / polygons / closure
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import bpy  # must precede bmesh when running as the bpy module
import bmesh
import numpy as np
from mathutils.bvhtree import BVHTree

JOB: Path
RESULT: dict = {"ops": [], "engine": "Blender " + bpy.app.version_string}


def to_blender(v):
    return np.asarray(v, float)[:, [0, 2, 1]] * [1, -1, 1]


def to_public(v):
    return np.asarray(v, float)[:, [0, 2, 1]] * [1, 1, -1]


def progress(i, total, op, detail=""):
    tmp = JOB / "progress.next.json"
    tmp.write_text(json.dumps(dict(index=i, total=total, op=op, detail=detail, t=time.time())), encoding="utf-8")
    for _ in range(4):
        try:
            tmp.replace(JOB / "progress.json")
            break
        except PermissionError:
            time.sleep(0.01)


def activate(obj):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def obj_of(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"UNKNOWN_PART:{name}")
    return obj


def metrics(obj) -> dict:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    out = dict(
        polygons=len(bm.faces),
        quads=sum(len(f.verts) == 4 for f in bm.faces),
        open_edges=sum(e.is_boundary for e in bm.edges),
        nonmanifold_edges=sum((not e.is_manifold) and (not e.is_boundary) for e in bm.edges),
        vertex_link_defects=sum(not v.is_manifold for v in bm.verts),
        degenerate_faces=sum(f.calc_area() < 1e-10 for f in bm.faces),
        volume_mm3=float(bm.calc_volume(signed=True)),
    )
    # connected shells
    seen, shells = set(), 0
    for f in bm.faces:
        if f.index in seen:
            continue
        shells += 1
        stack = [f]
        while stack:
            g = stack.pop()
            if g.index in seen:
                continue
            seen.add(g.index)
            for e in g.edges:
                stack.extend(x for x in e.link_faces if x.index not in seen)
    out["shells"] = shells
    out["quad_ratio"] = round(out["quads"] / max(1, out["polygons"]), 4)
    out["closed_manifold"] = bool(out["polygons"] and not any(
        out[k] for k in ("open_edges", "nonmanifold_edges", "vertex_link_defects", "degenerate_faces"))
        and out["volume_mm3"] > 0)
    bm.free()
    return out


def self_intersection_screen(obj, limit=250_000) -> dict:
    if len(obj.data.polygons) > limit:
        return dict(status="NOT_VERIFIED", reason="POLYGON_BUDGET")
    obj.data.calc_loop_triangles()
    tris = [tuple(t.vertices) for t in obj.data.loop_triangles]
    tree = BVHTree.FromPolygons([v.co for v in obj.data.vertices], tris, all_triangles=True, epsilon=1e-7)
    pairs = sum(1 for a, b in tree.overlap(tree) if a < b and not set(tris[a]).intersection(tris[b]))
    return dict(status="FAIL" if pairs else "PASS", pairs=pairs,
                method="BVH non-adjacent triangle overlap screen (not an exact proof)")


def new_object(name, verts_pub, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(to_blender(verts_pub).tolist(), [], np.asarray(faces).tolist())
    mesh.validate(clean_customdata=False)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def op_load(a):
    with np.load(JOB / a["file"], allow_pickle=False) as raw:
        obj = new_object(a["name"], raw["vertices"], raw["faces"])
    return dict(part=a["name"], metrics=metrics(obj))


def op_join(a):
    objs = [obj_of(n) for n in a["parts"]]
    activate(objs[0])
    for o in objs[1:]:
        o.select_set(True)
    bpy.ops.object.join()
    target = bpy.context.view_layer.objects.active
    target.name = a["into"]
    target.data.name = a["into"]
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=a.get("weld_mm", 1e-4))
    bm.to_mesh(target.data)
    bm.free()
    return dict(into=a["into"], merged=a["parts"], metrics=metrics(target))


def op_separate(a):
    src = obj_of(a["source"])
    with np.load(JOB / a["labels"], allow_pickle=False) as raw:
        labels = raw["labels"]
    if len(labels) != len(src.data.polygons):
        raise ValueError("LABEL_COUNT_MISMATCH")
    created = []
    verts = to_public([v.co[:] for v in src.data.vertices])
    polys = [list(p.vertices) for p in src.data.polygons]
    for k, name in enumerate(a["names"]):
        sel = [polys[i] for i in np.flatnonzero(labels == k)]
        used = sorted({v for f in sel for v in f})
        remap = {v: i for i, v in enumerate(used)}
        obj = new_object(name, verts[used], [[remap[v] for v in f] for f in sel])
        created.append(dict(part=name, metrics=metrics(obj)))
    bpy.data.objects.remove(src, do_unlink=True)
    return dict(source=a["source"], created=created)


def op_append(a):
    """Bring objects saved by per-part processes into this scene (assembly for export)."""
    path = str(JOB / a["blend"])
    with bpy.data.libraries.load(path, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n in a["objects"]]
    for obj in dst.objects:
        if obj is not None:
            bpy.context.collection.objects.link(obj)
    return dict(blend=a["blend"], objects=a["objects"])


def op_replace(a):
    """After a clean re-split: drop the old objects and give their names to the new ones."""
    for old, new in zip(a["old"], a["new"]):
        bpy.data.objects.remove(obj_of(old), do_unlink=True)
        o = obj_of(new)
        o.name = old
        o.data.name = old
    return dict(replaced=a["old"])


def fill_holes(obj) -> dict:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = sum(f.calc_area() for f in bm.faces)
    res = bmesh.ops.holes_fill(bm, edges=list(bm.edges), sides=0)
    caps = res.get("faces", [])
    cap_area = sum(f.calc_area() for f in caps)
    if caps:
        bmesh.ops.triangulate(bm, faces=caps)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return dict(caps=len(caps), cap_area_ratio=float(cap_area / max(before + cap_area, 1e-9)))


def boundary_length(obj) -> float:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    total = sum(e.calc_length() for e in bm.edges if e.is_boundary)
    bm.free()
    return float(total)


def voxelize(obj, voxel):
    activate(obj)
    obj.data.remesh_voxel_size = voxel
    obj.data.remesh_voxel_adaptivity = 0.0
    bpy.ops.object.voxel_remesh()


def apply_modifier(obj, kind, **props):
    activate(obj)
    mod = obj.modifiers.new(kind.lower(), kind)
    for k, v in props.items():
        setattr(mod, k, v)
    bpy.ops.object.modifier_apply(modifier=mod.name)


def keep_largest_shell(obj) -> float:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    groups, seen = [], set()
    for f in bm.faces:
        if f in seen:
            continue
        comp, stack = [], [f]
        while stack:
            g = stack.pop()
            if g in seen:
                continue
            seen.add(g)
            comp.append(g)
            for e in g.edges:
                stack.extend(x for x in e.link_faces if x not in seen)
        groups.append(comp)
    removed = 0.0
    if len(groups) > 1:
        groups.sort(key=lambda c: sum(f.calc_area() for f in c), reverse=True)
        total = sum(f.calc_area() for c in groups for f in c)
        drop = [f for c in groups[1:] for f in c]
        removed = sum(f.calc_area() for f in drop) / max(total, 1e-9)
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    return float(removed)


def surface_deviation(ref_mesh, obj, samples=3000) -> dict:
    ref_tree = BVHTree.FromPolygons([v.co for v in ref_mesh.vertices], [tuple(p.vertices) for p in ref_mesh.polygons])
    new_tree = BVHTree.FromPolygons([v.co for v in obj.data.vertices], [tuple(p.vertices) for p in obj.data.polygons])
    nv, rv = obj.data.vertices, ref_mesh.vertices
    fwd = [ref_tree.find_nearest(nv[k].co)[3] for k in range(0, len(nv), max(1, len(nv) // samples))]
    rev = [new_tree.find_nearest(rv[k].co)[3] for k in range(0, len(rv), max(1, len(rv) // samples))]
    err = np.asarray([e for e in fwd + rev if e is not None])
    return dict(p50_mm=float(np.percentile(err, 50)), p95_mm=float(np.percentile(err, 95)), max_mm=float(err.max()))


def op_rebuild(a):
    """Reference part -> NEW closed solid: fill/solidify -> voxel remesh -> smooth -> QuadriFlow.

    The reference is only a volume guide; the output surface is regenerated (not the AI triangles).
    """
    obj = obj_of(a["part"])
    reference = obj.data.copy()
    rec = dict(part=a["part"], input=metrics(obj))
    voxel_req = float(a["voxel_mm"])
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # Seams between AI fragments rarely share exact vertices. Welding below half a voxel loses nothing
    # the voxel rebuild could keep, and closes hair-line gaps that would otherwise look like open sheets.
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=max(1e-4, voxel_req * 0.5))
    bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=1e-6)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    mode = a.get("mode", "auto")
    if mode in ("auto", "solid"):
        fill = fill_holes(obj)
        fill["open_edges_after_fill"] = metrics(obj)["open_edges"]
        fill["residual_boundary_mm"] = boundary_length(obj)
        area = sum(p.area for p in obj.data.polygons)
        rec["fill"] = fill
        # Sheet-like = large lids or a long boundary that cannot be capped. Tiny residual cracks between AI
        # fragments are sealed by the voxel level set and must NOT turn a head into a hollow shell.
        if mode == "auto" and (fill["cap_area_ratio"] > 0.25 or fill["residual_boundary_mm"] > 0.5 * area ** 0.5):
            # Sheet-like part (cape, skirt): hole caps would be arbitrary lids. Use a designed shell instead.
            obj.data = reference.copy()
            mode = "shell"
        else:
            mode = "solid"
    if mode == "shell":
        apply_modifier(obj, "SOLIDIFY", thickness=float(a.get("shell_mm", 1.2)), offset=0.0,
                       use_rim=True, use_even_offset=True, use_quality_normals=True)
    rec["closure_mode"] = mode
    voxel = float(a["voxel_mm"])
    dims = obj.dimensions
    voxel = max(0.02, min(voxel, min(d for d in dims if d > 1e-6) / 10))
    voxelize(obj, voxel)
    rec["voxel_mm"] = round(voxel, 4)
    rec["removed_fragment_area_ratio"] = keep_largest_shell(obj)
    if a.get("smooth_iterations", 0):
        # Smooth away AI surface noise, then re-voxelize: the level set of a closed volume cannot
        # self-intersect, which removes folds that smoothing creates at sharp (e.g. carved) creases.
        apply_modifier(obj, "SMOOTH", factor=0.5, iterations=int(a["smooth_iterations"]) * 2)
        voxelize(obj, voxel)
        keep_largest_shell(obj)
    voxel_metrics = metrics(obj)
    rec["voxel_metrics"] = voxel_metrics
    rec["retopology"] = "voxel_quads"
    target = int(a.get("target_quads", 4000))
    if voxel_metrics["closed_manifold"] and voxel_metrics["polygons"] > target * 1.3:
        backup = obj.data.copy()
        activate(obj)
        if len(obj.data.polygons) > target * 8:
            # QuadriFlow cost grows with input size; a working copy of 8x target keeps the shape.
            apply_modifier(obj, "DECIMATE", ratio=target * 8 / len(obj.data.polygons))
        working = obj.data.copy()
        attempts = []
        for seed in (7, 23, 51):
            obj.data = working.copy()
            activate(obj)
            try:
                outcome = bpy.ops.object.quadriflow_remesh(target_faces=target, use_mesh_symmetry=False,
                                                           use_preserve_sharp=False, use_preserve_boundary=False,
                                                           smooth_normals=False, seed=seed)
            except RuntimeError as exc:
                outcome = {"CANCELLED"}
                rec["quadriflow_error"] = str(exc)[:300]
            after = metrics(obj)
            dev = surface_deviation(backup, obj) if after["polygons"] else dict(p95_mm=1e9, max_mm=1e9)
            inter = self_intersection_screen(obj) if after["closed_manifold"] else dict(status="NOT_VERIFIED")
            ok = ("FINISHED" in outcome and after["closed_manifold"] and after["shells"] == 1
                  and dev["p95_mm"] <= voxel * 2.5 and inter["status"] == "PASS")
            attempts.append(dict(seed=seed, outcome=sorted(outcome), closed=after["closed_manifold"], shells=after["shells"],
                                 deviation_p95_mm=dev["p95_mm"], self_intersection=inter.get("pairs", inter["status"]), accepted=ok))
            if ok:
                rec["retopology"] = "quadriflow"
                rec["quadriflow_deviation"] = dev
                break
        else:
            obj.data = backup
            rec["retopology"] = "voxel_quads (quadriflow rejected)"
        rec["quadriflow_attempts"] = attempts
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    rec["reference_deviation"] = surface_deviation(reference, obj)
    rec["metrics"] = metrics(obj)
    rec["self_intersection"] = self_intersection_screen(obj)
    for p in obj.data.polygons:
        p.use_smooth = True
    return rec


def op_save_npz(a):
    obj = obj_of(a["part"])
    obj.data.calc_loop_triangles()
    verts = to_public([v.co[:] for v in obj.data.vertices])
    faces = np.array([tuple(t.vertices) for t in obj.data.loop_triangles], dtype=np.int64)
    np.savez_compressed(JOB / a["file"], vertices=verts, faces=faces)
    return dict(part=a["part"], triangles=len(faces), metrics=metrics(obj))


def op_export(a):
    bpy.ops.wm.save_as_mainfile(filepath=str(JOB / a["blend"]))
    bpy.ops.wm.obj_export(filepath=str(JOB / a["obj"]), export_selected_objects=False, export_materials=False,
                          export_triangulated_mesh=False, forward_axis="NEGATIVE_Z", up_axis="Y")
    return dict(blend=a["blend"], obj=a["obj"], objects=sorted(o.name for o in bpy.data.objects if o.type == "MESH"))


def op_roundtrip(a):
    before = {o.name: metrics(o) for o in bpy.data.objects if o.type == "MESH"}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=str(JOB / a["obj"]), forward_axis="NEGATIVE_Z", up_axis="Y")
    after = {o.name: metrics(o) for o in bpy.data.objects if o.type == "MESH"}
    rows, ok = [], set(before) == set(after)
    for name in sorted(before):
        b, c = before[name], after.get(name)
        # OBJ splits vertices only by position; welded closure must survive.
        same = bool(c and c["polygons"] == b["polygons"] and c["quads"] == b["quads"]
                    and c["closed_manifold"] == b["closed_manifold"])
        ok &= same
        rows.append(dict(part=name, before=b["polygons"], after=c and c["polygons"], closed_after=c and c["closed_manifold"], same=same))
    return dict(verdict="PASS" if ok else "FAIL", parts=rows)


OPS = dict(append=op_append, load=op_load, join=op_join, separate=op_separate, replace=op_replace, rebuild=op_rebuild,
           save_npz=op_save_npz, export=op_export, roundtrip=op_roundtrip)


def main(job: Path) -> int:
    global JOB
    JOB = job
    plan = json.loads((job / "plan.json").read_text(encoding="utf-8"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 0.001
    ops = plan["ops"]
    failed = False
    for i, op in enumerate(ops):
        kind = op.get("op")
        progress(i, len(ops), kind, op.get("part") or op.get("into") or "")
        started = time.monotonic()
        try:
            if kind not in OPS:
                raise ValueError(f"UNSUPPORTED_OP:{kind}")
            out = OPS[kind](op)
            RESULT["ops"].append(dict(op=kind, status="done", seconds=round(time.monotonic() - started, 3), **out))
        except Exception as exc:  # recorded, never converted into success
            failed = True
            RESULT["ops"].append(dict(op=kind, status="error", error=f"{type(exc).__name__}: {exc}",
                                      trace=traceback.format_exc()[-1500:], request=op))
            if op.get("required", True):
                break
    RESULT["status"] = "error" if failed else "done"
    (job / "result.json").write_text(json.dumps(RESULT, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    argv = sys.argv
    target = Path(argv[argv.index("--") + 1] if "--" in argv else argv[1]).resolve()
    sys.exit(main(target))
