#!/usr/bin/env python3
"""ruler — 给页面图叠一把「尺子」：上边与左边每 10% 一个刻度与数字。

不是强制网格选择，只是给模型一个可读的坐标参照；模型仍然自由给坐标。
用来检验「坐标读不准」到底是不是瓶颈。
"""
from __future__ import annotations
import argparse, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "skills", "pdf-figures", "scripts"))
from PIL import ImageDraw
from pdfx import Doc

def ruled(doc, page=0, dpi=150, step=0.1):
    im = doc.render(page, dpi)
    W, H = im.size
    d = ImageDraw.Draw(im)
    n = int(round(1 / step))
    for k in range(n + 1):
        v = k * step
        x, y = min(W - 1, int(v * W)), min(H - 1, int(v * H))
        d.line([(x, 0), (x, H)], fill=(255, 120, 120), width=1)
        d.line([(0, y), (W, y)], fill=(255, 120, 120), width=1)
        lab = f"{v:.1f}"
        d.rectangle([x + 1, 1, x + 24, 13], fill=(255, 255, 255))
        d.text((x + 3, 2), lab, fill=(200, 0, 0))
        d.rectangle([1, y + 1, 24, y + 13], fill=(255, 255, 255))
        d.text((2, y + 2), lab, fill=(200, 0, 0))
    return im

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=150)
    a = ap.parse_args()
    mapping = json.load(open(a.mapping, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    for name in mapping:
        doc = Doc(os.path.join(a.pages, name))
        ruled(doc, 0, a.dpi).save(os.path.join(a.out, name))
    print(json.dumps({"pages": len(mapping), "out": a.out}))

if __name__ == "__main__":
    raise SystemExit(main())
