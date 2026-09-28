"""GLB validation, part loading, NPZ persistence and GLB export. Coordinates are mm, Y-up."""
from __future__ import annotations

import colorsys
import hashlib
import io
import json
import struct
from pathlib import Path

import numpy as np
import trimesh

MAX_FACES = 4_000_000
MAX_PARTS = 128


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_glb(data: bytes, max_bytes: int) -> dict:
    """Structural GLB 2.0 check. Open boundaries are allowed: this validates the container only."""
    if len(data) < 20 or len(data) > max_bytes:
        raise ValueError(f"GLB size must be 20 B .. {max_bytes // (1024 * 1024)} MiB")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError("Invalid GLB 2.0 header or length")
    offset, doc = 12, None
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Truncated GLB chunk")
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if offset + size > len(data):
            raise ValueError("Invalid GLB chunk length")
        if doc is None:
            if kind != 0x4E4F534A:
                raise ValueError("First GLB chunk must be JSON")
            doc = json.loads(data[offset:offset + size])
        offset += size
    if not isinstance(doc, dict) or not doc.get("meshes"):
        raise ValueError("GLB contains no meshes")
    for entry in doc.get("buffers", []) + doc.get("images", []):
        if "uri" in entry:
            raise ValueError("External / data URIs are not accepted; embed buffers in the GLB")
    primitives = [p for m in doc["meshes"] for p in m.get("primitives", [])]
    if any(p.get("mode", 4) != 4 for p in primitives):
        raise ValueError("Only triangle primitives are supported")
    return doc


def load_parts(data: bytes, *, height_mm: float, up_axis: str = "Y", max_bytes: int = 120 << 20) -> dict[str, trimesh.Trimesh]:
    """Scene nodes -> world-space parts, scaled so that model height == height_mm, feet at y=0."""
    validate_glb(data, max_bytes)
    scene = trimesh.load(io.BytesIO(data), file_type="glb", force="scene", process=False)
    parts: dict[str, trimesh.Trimesh] = {}
    for node in sorted(scene.graph.nodes_geometry):
        transform, key = scene.graph[node]
        geom = scene.geometry[key]
        if not isinstance(geom, trimesh.Trimesh) or not len(geom.faces):
            continue
        mesh = trimesh.Trimesh(np.asarray(geom.vertices, float), np.asarray(geom.faces), process=False)
        mesh.apply_transform(transform)
        if not np.isfinite(mesh.vertices).all():
            raise ValueError("Non-finite coordinates")
        name = safe_name(node, parts)
        parts[name] = mesh
    if not parts or len(parts) > MAX_PARTS or sum(len(m.faces) for m in parts.values()) > MAX_FACES:
        raise ValueError(f"Empty input or budget exceeded ({MAX_PARTS} parts / {MAX_FACES} triangles)")
    if up_axis.upper() == "Z":
        for m in parts.values():
            m.vertices = m.vertices[:, [0, 2, 1]] * [1, 1, -1]
    normalize(parts, height_mm)
    for m in parts.values():
        weld(m)
    return parts


def safe_name(raw: str, taken) -> str:
    base = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(raw))[:48] or "part"
    name, i = base, 1
    while name in taken:
        i += 1
        name = f"{base}_{i}"
    return name


def normalize(parts: dict[str, trimesh.Trimesh], height_mm: float) -> None:
    low = np.min([m.bounds[0] for m in parts.values()], axis=0)
    high = np.max([m.bounds[1] for m in parts.values()], axis=0)
    span = high[1] - low[1]
    if span <= 1e-9:
        raise ValueError("Model has zero height; check the up axis")
    origin = np.array([(low[0] + high[0]) / 2, low[1], (low[2] + high[2]) / 2])
    for m in parts.values():
        m.vertices = (m.vertices - origin) * (height_mm / span)


def weld(mesh: trimesh.Trimesh, digits: int = 5) -> trimesh.Trimesh:
    """GLB splits vertices by normal/UV. Merge by position so seams are not counted as holes."""
    mesh.merge_vertices(digits_vertex=digits, merge_norm=True, merge_tex=True)
    mesh.update_faces(mesh.nondegenerate_faces(height=1e-10))
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def save_parts(parts: dict[str, trimesh.Trimesh], folder: Path, extra: dict | None = None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, m in parts.items():
        np.savez_compressed(folder / f"{name}.npz", vertices=np.asarray(m.vertices, np.float64),
                            faces=np.asarray(m.faces, np.int64))
        index[name] = {"file": f"{name}.npz"}
    manifest = {"parts": index, "geometry_sha256": geometry_digest(parts), **(extra or {})}
    (folder / "parts.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


def load_saved(folder: Path) -> dict[str, trimesh.Trimesh]:
    manifest = json.loads((folder / "parts.json").read_text(encoding="utf-8"))
    parts = {}
    for name, row in manifest["parts"].items():
        with np.load(folder / row["file"]) as raw:
            parts[name] = trimesh.Trimesh(raw["vertices"], raw["faces"], process=False)
    return parts


def geometry_digest(parts: dict[str, trimesh.Trimesh]) -> str:
    h = hashlib.sha256()
    for name in sorted(parts):
        m = parts[name]
        h.update(name.encode())
        h.update(np.ascontiguousarray(m.vertices, "<f8").tobytes())
        h.update(np.ascontiguousarray(m.faces, "<i8").tobytes())
    return h.hexdigest()


def part_color(i: int, sat: float = 0.35) -> list[int]:
    r, g, b = colorsys.hsv_to_rgb((i * 0.618034) % 1, sat, 0.85)
    return [int(r * 255), int(g * 255), int(b * 255), 255]


def export_glb(parts: dict[str, trimesh.Trimesh], colors: dict[str, list[int]] | None = None) -> bytes:
    """Viewer GLB: glTF metres, one node per part (names preserved)."""
    scene = trimesh.Scene()
    for i, (name, mesh) in enumerate(parts.items()):
        m = trimesh.Trimesh(np.asarray(mesh.vertices) * 0.001, np.asarray(mesh.faces), process=False)
        rgba = np.array((colors or {}).get(name) or part_color(i), dtype=np.uint8)
        m.visual = trimesh.visual.ColorVisuals(m, face_colors=np.tile(rgba, (len(m.faces), 1)))
        scene.add_geometry(m, node_name=name, geom_name=name)
    return scene.export(file_type="glb")
