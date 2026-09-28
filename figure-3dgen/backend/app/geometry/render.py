"""Dependency-free orthographic review renderer (surface point splatting + z-buffer) and
silhouette utilities used to compare the model with approved photo views.

Views (model is Y-up, front faces +Z):
  front: camera on +Z, screen-right = +X     back: camera on -Z, screen-right = -X
  right: camera on +X, screen-right = -Z     left: camera on -X, screen-right = +Z
"""
from __future__ import annotations

import io

import numpy as np
import trimesh
from PIL import Image
from scipy import ndimage

VIEWS = {
    "front": (np.array([0, 0, 1.0]), np.array([1.0, 0, 0])),
    "right": (np.array([1.0, 0, 0]), np.array([0, 0, -1.0])),
    "back": (np.array([0, 0, -1.0]), np.array([-1.0, 0, 0])),
    "left": (np.array([-1.0, 0, 0]), np.array([0, 0, 1.0])),
}
LIGHT = np.array([0.35, 0.6, 0.72])


def frame(bounds: np.ndarray, size: int, margin: float = 0.06) -> dict:
    """Common orthographic framing: model height fills (1 - 2*margin) of the image."""
    low, high = bounds
    height = max(high[1] - low[1], 1e-6)
    scale = size * (1 - 2 * margin) / height
    center = (low + high) / 2
    return dict(scale=scale, center=center, size=size)


def project(points: np.ndarray, view: str, fr: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cam, right = VIEWS[view]
    rel = points - fr["center"]
    u = rel @ right * fr["scale"] + fr["size"] / 2
    v = fr["size"] / 2 - rel[:, 1] * fr["scale"]
    depth = rel @ cam
    return u, v, depth


def sample(parts: dict[str, trimesh.Trimesh], total: int, seed: int = 3):
    areas = np.array([max(m.area, 1e-9) for m in parts.values()])
    counts = np.maximum(200, (areas / areas.sum() * total).astype(int))
    rng = np.random.default_rng(seed)
    out = []
    for (name, m), n in zip(parts.items(), counts):
        if not len(m.faces):
            continue
        prob = m.area_faces / max(m.area_faces.sum(), 1e-12)
        fid = rng.choice(len(m.faces), size=n, p=prob)
        r = rng.random((n, 2))
        flip = r.sum(1) > 1
        r[flip] = 1 - r[flip]
        tri = m.triangles[fid]
        pts = tri[:, 0] + r[:, :1] * (tri[:, 1] - tri[:, 0]) + r[:, 1:] * (tri[:, 2] - tri[:, 0])
        out.append((name, pts, m.face_normals[fid]))
    return out


def render(parts: dict[str, trimesh.Trimesh], view: str, *, size: int = 512, colors: dict[str, list[int]] | None = None,
           bounds: np.ndarray | None = None, highlight: set[str] | None = None, points: int | None = None) -> Image.Image:
    bounds = bounds if bounds is not None else np.array([np.min([m.bounds[0] for m in parts.values()], 0),
                                                         np.max([m.bounds[1] for m in parts.values()], 0)])
    fr = frame(bounds, size)
    samples = sample(parts, points or size * size * 3)
    cam, _ = VIEWS[view]
    uu, vv, dd, cc = [], [], [], []
    for i, (name, pts, nrm) in enumerate(samples):
        u, v, d = project(pts, view, fr)
        base = np.array((colors or {}).get(name, [200, 200, 205, 255])[:3], float)
        if highlight is not None:
            base = np.array([255, 90, 60.0]) if name in highlight else np.array([185, 188, 195.0])
        facing = np.abs(nrm @ cam)
        shade = 0.35 + 0.65 * np.clip(np.abs(nrm @ (LIGHT / np.linalg.norm(LIGHT))) * 0.6 + facing * 0.4, 0, 1)
        uu.append(u); vv.append(v); dd.append(d); cc.append(shade[:, None] * base[None, :])
    u, v, d, c = (np.concatenate(x) for x in (uu, vv, dd, cc))
    ui, vi = u.astype(int), v.astype(int)
    ok = (ui >= 0) & (ui < size) & (vi >= 0) & (vi < size)
    pix = vi[ok] * size + ui[ok]
    order = np.lexsort((-d[ok], pix))
    first = np.unique(pix[order], return_index=True)[1]
    img = np.full((size * size, 4), [246, 247, 249, 0], np.uint8)
    chosen = order[first]
    img[pix[chosen], :3] = np.clip(c[ok][chosen], 0, 255).astype(np.uint8)
    img[pix[chosen], 3] = 255
    img = img.reshape(size, size, 4)
    # close 1-px splat gaps
    alpha = ndimage.binary_closing(img[..., 3] > 0, iterations=1)
    fill = alpha & (img[..., 3] == 0)
    if fill.any():
        blurred = ndimage.grey_dilation(img[..., :3], size=(3, 3, 1))
        img[fill, :3] = blurred[fill]
        img[fill, 3] = 255
    return Image.fromarray(img, "RGBA")


def model_mask(parts: dict[str, trimesh.Trimesh], view: str, size: int, bounds: np.ndarray) -> np.ndarray:
    img = render(parts, view, size=size, bounds=bounds, points=size * size * 2)
    return ndimage.binary_fill_holes(ndimage.binary_closing(np.asarray(img)[..., 3] > 0, iterations=2))


def photo_mask(image: Image.Image) -> np.ndarray:
    """Foreground of a single-subject view: alpha if present, else distance from border background colour."""
    rgba = np.asarray(image.convert("RGBA")).astype(float)
    if (rgba[..., 3] < 250).mean() > 0.02:
        mask = rgba[..., 3] > 128
    else:
        rgb = rgba[..., :3]
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        bg = np.median(border, axis=0)
        mask = np.linalg.norm(rgb - bg, axis=-1) > 28
    mask = ndimage.binary_opening(mask, iterations=1)
    labels, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    keep = np.flatnonzero(sizes >= max(sizes.max() * 0.02, 20)) + 1
    return ndimage.binary_fill_holes(np.isin(labels, keep))


def normalize_mask(mask: np.ndarray, size: int, margin: float = 0.06) -> np.ndarray:
    """Crop to the silhouette, scale by height to the common frame, centre horizontally."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return np.zeros((size, size), bool)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    target_h = int(round(size * (1 - 2 * margin)))
    scale = target_h / crop.shape[0]
    target_w = max(1, int(round(crop.shape[1] * scale)))
    img = Image.fromarray(crop.astype(np.uint8) * 255).resize((min(target_w, size), target_h), Image.NEAREST)
    out = np.zeros((size, size), bool)
    arr = np.asarray(img) > 127
    top = int(round(size * margin))
    left = (size - arr.shape[1]) // 2
    out[top:top + arr.shape[0], left:left + arr.shape[1]] = arr
    return out


def compare(model: np.ndarray, photo: np.ndarray) -> dict:
    inter = (model & photo).sum()
    union = (model | photo).sum()
    return dict(iou=round(float(inter / union), 4) if union else 0.0,
                excess_ratio=round(float((model & ~photo).sum() / max(photo.sum(), 1)), 4),
                deficit_ratio=round(float((photo & ~model).sum() / max(photo.sum(), 1)), 4))


def diff_image(model: np.ndarray, photo: np.ndarray) -> Image.Image:
    """grey = agree, red = model outside photo (excess), blue = photo not covered (deficit)."""
    img = np.full(model.shape + (3,), 250, np.uint8)
    img[model & photo] = [150, 155, 165]
    img[model & ~photo] = [235, 70, 60]
    img[~model & photo] = [60, 110, 235]
    return Image.fromarray(img)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
