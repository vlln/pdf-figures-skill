#!/usr/bin/env python3
"""pixels — 渲染、裁剪与墨迹分析的小工具（Pillow；PDF 输入另需 pypdfium2）。

所有坐标都是相对整页的归一化 0..1（左上原点）。输出都是一行 JSON。

子命令：
  render  <input> --out p.png [--dpi 200]
  crop    <input> --box x0,y0,x1,y1 --out c.png [--dpi 300] [--tight]
  bbox    <input> --box x0,y0,x1,y1                       # 区域内墨迹包围盒
  profile <input> --box x0,y0,x1,y1 --axis row|col [--min-gap 0.01] [--out bands.png]
"""

from __future__ import annotations

import argparse
import json
import sys

from PIL import Image, ImageDraw, ImageOps

BASE_DPI = 200  # 分析用渲染；坐标是归一化的，所以 dpi 只影响精度，不影响数值含义


def load(path: str, page: int = 1, dpi: int = BASE_DPI) -> Image.Image:
    with open(path, "rb") as fh:
        is_pdf = fh.read(5) == b"%PDF-"
    if not is_pdf:
        return Image.open(path).convert("RGB")
    try:
        import pypdfium2
    except ImportError:
        sys.exit("pixels: PDF input needs pypdfium2 (pip install pypdfium2)")
    doc = pypdfium2.PdfDocument(path)
    return doc[page - 1].render(scale=dpi / 72.0).to_pil().convert("RGB")


def parse_box(text: str):
    vals = [float(v) for v in text.replace(" ", "").split(",")]
    if len(vals) != 4 or not all(0.0 <= v <= 1.0 for v in vals) or vals[2] <= vals[0] or vals[3] <= vals[1]:
        raise SystemExit(f"pixels: box must be x0,y0,x1,y1 in 0..1 with x1>x0, y1>y0; got {text!r}")
    return vals


def region(im: Image.Image, box):
    W, H = im.size
    px = (int(box[0] * W), int(box[1] * H), max(int(box[0] * W) + 1, int(round(box[2] * W))),
          max(int(box[1] * H) + 1, int(round(box[3] * H))))
    return im.crop(px), px


def ink_mask(im: Image.Image) -> Image.Image:
    """255 = 墨迹。背景取直方图众数；暗底先反相；阈值 = 背景 − 18，夹在 [150, 245]。"""
    g = im.convert("L")
    hist = g.histogram()
    bg = max(range(256), key=lambda v: hist[v])
    if bg < 128:
        g = ImageOps.invert(g)
        bg = 255 - bg
    th = max(150, min(245, bg - 18))
    return g.point(lambda v: 255 if v < th else 0)


def to_norm(px_box, origin, page_size):
    W, H = page_size
    ox, oy = origin
    return [round((ox + px_box[0]) / W, 4), round((oy + px_box[1]) / H, 4),
            round((ox + px_box[2]) / W, 4), round((oy + px_box[3]) / H, 4)]


def cmd_render(a):
    im = load(a.input, a.page, a.dpi)
    im.save(a.out)
    print(json.dumps({"out": a.out, "px": list(im.size), "dpi": a.dpi}))


def cmd_crop(a):
    box = parse_box(a.box)
    im = load(a.input, a.page, a.dpi)
    sub, px = region(im, box)
    info = {"box": box, "px": list(sub.size)}
    bb = ink_mask(sub).getbbox()
    if bb:
        w, h = sub.size
        info["ink_box"] = to_norm(bb, px[:2], im.size)
        info["ink_touches_edge"] = {"left": bb[0] <= 1, "top": bb[1] <= 1,
                                    "right": bb[2] >= w - 1, "bottom": bb[3] >= h - 1}
        if a.tight:
            pad = max(2, h // 150)
            sub = sub.crop((max(0, bb[0] - pad), max(0, bb[1] - pad), min(w, bb[2] + pad), min(h, bb[3] + pad)))
            info["px"] = list(sub.size)
    sub.save(a.out)
    info["out"] = a.out
    print(json.dumps(info))


def cmd_bbox(a):
    box = parse_box(a.box)
    im = load(a.input, a.page)
    sub, px = region(im, box)
    bb = ink_mask(sub).getbbox()
    print(json.dumps({"box": box, "ink_box": to_norm(bb, px[:2], im.size) if bb else None}))


def cmd_profile(a):
    box = parse_box(a.box)
    im = load(a.input, a.page)
    sub, px = region(im, box)
    mask = ink_mask(sub)
    w, h = sub.size
    if a.axis == "row":
        prof, n = list(mask.resize((1, h), Image.BOX).tobytes()), h
    else:
        prof, n = list(mask.resize((w, 1), Image.BOX).tobytes()), w
    page_n = im.size[1] if a.axis == "row" else im.size[0]
    min_gap = max(1, int(a.min_gap * page_n))
    bands, start, blank = [], None, 0
    for i, v in enumerate(prof + [0] * (min_gap + 1)):
        if v > 0:
            if start is None:
                start = i
            blank = 0
        elif start is not None:
            blank += 1
            if blank >= min_gap:
                bands.append((start, min(n, i - blank + 1)))
                start, blank = None, 0
    off = px[1] if a.axis == "row" else px[0]
    out_bands = [{"i": k + 1, "from": round((off + s) / page_n, 4), "to": round((off + e) / page_n, 4)}
                 for k, (s, e) in enumerate(bands)]
    info = {"axis": a.axis, "box": box, "bands": out_bands}
    if a.out:
        vis = sub.copy()
        d = ImageDraw.Draw(vis)
        for k, (s, e) in enumerate(bands, 1):
            if a.axis == "row":
                d.rectangle([0, s, w - 1, e], outline=(0, 120, 255))
                d.text((3, s + 1), str(k), fill=(0, 80, 200))
            else:
                d.rectangle([s, 0, e, h - 1], outline=(0, 120, 255))
                d.text((s + 2, 3), str(k), fill=(0, 80, 200))
        vis.save(a.out)
        info["out"] = a.out
    print(json.dumps(info))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pixels")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render"); p.add_argument("input"); p.add_argument("--out", required=True)
    p.add_argument("--dpi", type=int, default=BASE_DPI)
    p = sub.add_parser("crop"); p.add_argument("input"); p.add_argument("--box", required=True)
    p.add_argument("--out", required=True); p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--tight", action="store_true")
    p = sub.add_parser("bbox"); p.add_argument("input"); p.add_argument("--box", required=True)
    p = sub.add_parser("profile"); p.add_argument("input"); p.add_argument("--box", required=True)
    p.add_argument("--axis", choices=["row", "col"], required=True)
    p.add_argument("--min-gap", type=float, default=0.01, help="最小空白间隔，占整页高/宽的比例")
    p.add_argument("--out", default="")
    for p in sub.choices.values():
        p.add_argument("--page", type=int, default=1, help="PDF 页号，从 1 开始")
    a = ap.parse_args(argv)
    {"render": cmd_render, "crop": cmd_crop, "bbox": cmd_bbox, "profile": cmd_profile}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
