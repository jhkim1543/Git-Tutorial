"""Stage 1 — intake. Photo (required, appearance truth) and AI GLB (reference, partition hint only)."""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from .. import jobs
from ..config import settings
from ..geometry import meshio
from ..geometry.audit import audit

MAX_PHOTO_PX = 4096


def normalize_photo(data: bytes) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("Photo must be PNG / JPEG / WebP") from None
    if img.width < 64 or img.height < 64:
        raise ValueError("Photo is too small")
    img.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX))
    buf = io.BytesIO()
    img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
    return buf.getvalue()


def run(job_id: str, photo: bytes, glb: bytes) -> dict:
    root = jobs.job_dir(job_id)
    job = jobs.load(job_id)
    opts = job["options"]
    inp = root / "intake"
    inp.mkdir(exist_ok=True)
    photo_png = normalize_photo(photo)
    (inp / "photo.png").write_bytes(photo_png)
    (inp / "reference.glb").write_bytes(glb)
    parts = meshio.load_parts(glb, height_mm=opts["height_mm"], up_axis=opts["up_axis"],
                              max_bytes=settings.max_upload_mb << 20)
    manifest = meshio.save_parts(parts, inp / "parts", dict(role="reference_only"))
    rows = [dict(part=n, **audit(m)) for n, m in parts.items()]
    report = dict(
        role=dict(photo="appearance ground truth (via approved multi-view)", glb="partition hypothesis only — not a shape answer"),
        photo_sha256=meshio.sha256(photo), glb_sha256=meshio.sha256(glb),
        reference_geometry_sha256=manifest["geometry_sha256"],
        parts=rows, part_count=len(rows), closed_parts=sum(r["closed"] for r in rows),
        open_edges_total=sum(r["open_edges"] for r in rows),
        note="Open / non-manifold input is accepted. Editable output must close every part.")
    (inp / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (inp / "reference-view.glb").write_bytes(meshio.export_glb(parts))
    return dict(status="done", summary=dict(parts=len(rows), closed=report["closed_parts"],
                                             open_edges=report["open_edges_total"]))


def load_reference(root: Path):
    return meshio.load_saved(root / "intake" / "parts")
