"""Stage 2 — multi-view ground truth from the ORIGINAL PHOTO (not from the GLB).

Astra generates orthographic turnaround views from the photo; a human approves or replaces each view.
Approved silhouettes become the appearance target for the Editable rebuild.
"""
from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image

from .. import astra, jobs
from ..geometry.render import normalize_mask, photo_mask, png_bytes
from .intake import normalize_photo

VIEW_NAMES = ("front", "right", "back", "left")
PROMPT = (
    "Using the attached ORIGINAL PHOTO of a collectible figure as the only reference, draw the SAME figure as a clean "
    "orthographic {view} view (camera exactly at the figure's {view} side, no perspective, no rotation, full body inside the "
    "frame with a small margin, feet at the bottom). Keep proportions, pose, hair, clothing, accessories and colours identical "
    "to the photo. Flat neutral studio lighting, plain pure white background, no shadow, no ground plane, no text. Where the "
    "{view} side is not visible in the photo, infer conservatively and keep it simple.")


def generate(job_id: str) -> dict:
    root = jobs.job_dir(job_id)
    out = root / "views"
    out.mkdir(exist_ok=True)
    photo = (root / "intake" / "photo.png").read_bytes()
    meta = {}
    for i, view in enumerate(VIEW_NAMES):
        jobs.progress(job_id, "views", i / len(VIEW_NAMES) * 100, f"Astra 다시점 생성 {view}")
        res = astra.generate_image(PROMPT.format(view=view), [("original-photo.png", photo)], cache=root / "cache" / "astra")
        (out / f"{view}.png").write_bytes(res["png"])
        meta[view] = dict(source="astra", provenance=res["provenance"], revised_prompt=res.get("revised_prompt"),
                          approved=False, from_photo=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(status="review", views=meta)


def upload(job_id: str, view: str, data: bytes | None, *, use_photo: bool = False) -> dict:
    if view not in VIEW_NAMES:
        raise ValueError("unknown view")
    root = jobs.job_dir(job_id)
    out = root / "views"
    out.mkdir(exist_ok=True)
    png = (root / "intake" / "photo.png").read_bytes() if use_photo else normalize_photo(data or b"")
    (out / f"{view}.png").write_bytes(png)
    meta = _meta(root)
    meta[view] = dict(source="original_photo" if use_photo else "user_upload", approved=False, from_photo=True)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return meta


def _meta(root) -> dict:
    p = root / "views" / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def approve(job_id: str, approved: list[str], size: int = 384) -> dict:
    """Freeze the approved views and their silhouettes. At least the front view is required."""
    root = jobs.job_dir(job_id)
    meta = _meta(root)
    approved = [v for v in approved if v in meta]
    if "front" not in approved:
        raise ValueError("the front view must be approved")
    masks = {}
    for v in VIEW_NAMES:
        meta.get(v, {})["approved"] = v in approved
        if v in approved:
            img = Image.open(io.BytesIO((root / "views" / f"{v}.png").read_bytes()))
            m = normalize_mask(photo_mask(img), size)
            if m.sum() < 50:
                raise ValueError(f"no foreground silhouette found in the {v} view")
            masks[v] = m
            (root / "views" / f"{v}-mask.png").write_bytes(png_bytes(Image.fromarray(m.astype(np.uint8) * 255)))
    np.savez_compressed(root / "views" / "masks.npz", **masks)
    (root / "views" / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return dict(status="approved", approved_views=approved, views=meta)


def load_masks(root) -> dict[str, np.ndarray]:
    p = root / "views" / "masks.npz"
    if not p.is_file():
        return {}
    with np.load(p) as raw:
        return {k: raw[k] for k in raw.files}
