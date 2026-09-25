#!/usr/bin/env python3
"""
Assemble the slide partials into one deck.html, render each slide to PNG at
1920x1080 with Chromium, then package the PNGs into a .pptx and a .pdf.

    python3 build.py            # html + png + pptx + pdf
    python3 build.py --html     # assemble deck.html only
    python3 build.py --png      # html + png only
"""
import glob
import os
import shutil
import subprocess
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
DIST = os.path.join(ROOT, "dist")
PNG = os.path.join(DIST, "slides")

CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
W, H = 1920, 1080
NAME = "김나영_포트폴리오_2026"

SHELL = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>김나영 Portfolio 2026</title>
<link rel="stylesheet" href="styles.css">
</head><body>
{body}
</body></html>
"""


def assemble():
    parts = sorted(glob.glob(os.path.join(SRC, "_*.html")))
    body = "\n\n".join(open(p, encoding="utf-8").read() for p in parts)
    out = os.path.join(SRC, "deck.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(SHELL.format(body=body))
    n = body.count('<section class="slide')
    print(f"deck.html  <- {len(parts)} partials, {n} slides")
    return out, n


def render(deck):
    from playwright.sync_api import sync_playwright

    shutil.rmtree(PNG, ignore_errors=True)
    os.makedirs(PNG, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--force-color-profile=srgb"])
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        pg.goto("file://" + deck)
        pg.wait_for_timeout(2500)          # webfonts + images
        slides = pg.query_selector_all("section.slide")
        for i, s in enumerate(slides, 1):
            s.screenshot(path=os.path.join(PNG, f"{i:02d}.png"))
        b.close()
    print(f"rendered   -> {len(slides)} png @ {W*2}x{H*2}")
    return len(slides)


def to_pptx():
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)   # 16:9
    blank = prs.slide_layouts[6]
    files = sorted(glob.glob(os.path.join(PNG, "*.png")))
    for f in files:
        s = prs.slides.add_slide(blank)
        s.shapes.add_picture(f, 0, 0, width=prs.slide_width, height=prs.slide_height)
    out = os.path.join(DIST, NAME + ".pptx")
    prs.save(out)
    print(f"pptx       -> {out}  ({len(files)} slides, {os.path.getsize(out)/1e6:.1f} MB)")


def to_pdf():
    from PIL import Image

    files = sorted(glob.glob(os.path.join(PNG, "*.png")))
    ims = [Image.open(f).convert("RGB") for f in files]
    out = os.path.join(DIST, NAME + ".pdf")
    ims[0].save(out, save_all=True, append_images=ims[1:], resolution=150.0)
    print(f"pdf        -> {out}  ({os.path.getsize(out)/1e6:.1f} MB)")


if __name__ == "__main__":
    deck, _ = assemble()
    if "--html" in sys.argv:
        sys.exit()
    render(deck)
    if "--png" in sys.argv:
        sys.exit()
    to_pptx()
    to_pdf()
