#!/usr/bin/env python3
"""Report any element whose box escapes its 1920x1080 slide."""
import os
from playwright.sync_api import sync_playwright

SRC = os.path.dirname(os.path.abspath(__file__))
JS = """
() => {
  const out = [];
  document.querySelectorAll('section.slide').forEach((s, i) => {
    const sb = s.getBoundingClientRect();
    s.querySelectorAll('*').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return;
      const over = {
        bottom: Math.round(r.bottom - sb.bottom),
        right:  Math.round(r.right  - sb.right),
        top:    Math.round(sb.top   - r.top),
        left:   Math.round(sb.left  - r.left),
      };
      const worst = Object.entries(over).filter(([, v]) => v > 2);
      if (!worst.length) return;
      // only report the element itself, not every ancestor that merely contains it
      if (el.querySelector('*') && [...el.children].some(c => {
            const cr = c.getBoundingClientRect();
            return cr.bottom - sb.bottom > 2 || cr.right - sb.right > 2;
          })) return;
      out.push({
        slide: i + 1,
        tag: el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ').join('.') : ''),
        over: worst.map(([k, v]) => `${k}+${v}`).join(' '),
        text: (el.textContent || '').trim().slice(0, 48),
      });
    });
  });
  // cards whose content exceeds their own fixed height
  document.querySelectorAll('section.slide').forEach((s, i) => {
    s.querySelectorAll('.card, .fig, .band').forEach(el => {
      if (el.scrollHeight - el.clientHeight > 2) {
        out.push({ slide: i + 1, tag: 'CLIPPED ' + el.className,
                   over: 'inner+' + (el.scrollHeight - el.clientHeight),
                   text: (el.textContent || '').trim().slice(0, 48) });
      }
    });
  });
  return out;
}
"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto("file://" + os.path.join(SRC, "deck.html"))
    pg.wait_for_timeout(2500)
    rows = pg.evaluate(JS)
    b.close()
if not rows:
    print("no overflow")
for r in rows:
    print(f"slide {r['slide']:>2}  {r['over']:<16} {r['tag'][:44]:<44} {r['text']}")
