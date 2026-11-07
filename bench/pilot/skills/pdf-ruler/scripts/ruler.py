#!/usr/bin/env python3
"""ruler — 四个原语：取图、刻度、画框、打包；外加一个坐标换算。

依赖：Pillow；输入是 PDF 时另需 pypdfium2。

坐标：
  - 输入给的都是**整页像素** x0,y0,x1,y1；可加页号前缀 "3:x0,y0,x1,y1"，省略则用 --page。
  - 输出图上的刻度是**本视图左上角起算的原图像素**（0,0 在角上）。
  - 换算回整页归一化坐标用 norm。
"""

from __future__ import annotations

import argparse
import json
import sys

from PIL import Image, ImageDraw

PDF_DPI = 200
BOX_COLORS = [(0, 150, 0), (200, 0, 200), (230, 180, 0), (0, 0, 0), (110, 0, 190)]


# ---------------------------------------------------------------- 输入


class Doc:
    def __init__(self, path: str):
        with open(path, "rb") as fh:
            self.is_pdf = fh.read(5) == b"%PDF-"
        self.path, self._cache, self._doc = path, {}, None

    def page(self, n: int) -> Image.Image:
        if n in self._cache:
            return self._cache[n]
        if not self.is_pdf:
            if n != 1:
                sys.exit(f"ruler: {self.path} is a single image, page must be 1")
            im = Image.open(self.path).convert("RGB")
        else:
            if self._doc is None:
                try:
                    import pypdfium2
                except ImportError:
                    sys.exit("ruler: PDF input needs pypdfium2 (pip install pypdfium2)")
                self._doc = pypdfium2.PdfDocument(self.path)
            if not 1 <= n <= len(self._doc):
                sys.exit(f"ruler: page {n} out of range (1..{len(self._doc)})")
            im = self._doc[n - 1].render(scale=PDF_DPI / 72.0).to_pil().convert("RGB")
        self._cache[n] = im
        return im


def parse_items(text: str, default_page: int) -> list[tuple[int, tuple | None]]:
    """'3:10,20,30,40;5:;10,20,30,40' -> [(3,(10,20,30,40)), (5,None), (default,(10,20,30,40))]。"""
    out = []
    for raw in text.split(";"):
        part = raw.strip()
        if not part:
            continue
        page, box = default_page, None
        head, sep, tail = part.partition(":")
        if sep:
            if not head.strip().isdigit():
                sys.exit(f"ruler: bad page prefix in {part!r}")
            page, part = int(head), tail.strip()
        if part:
            vals = [float(v) for v in part.replace(" ", "").split(",")]
            if len(vals) != 4:
                sys.exit(f"ruler: box needs 4 numbers, got {part!r}")
            box = tuple(vals)
        out.append((page, box))
    if not out:
        sys.exit("ruler: empty item list")
    return out


# ---------------------------------------------------------------- 叠层


def auto_step(scale: float, target_px: int = 90) -> int:
    raw = max(1.0, target_px / max(scale, 1e-6))
    for nice in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if nice >= raw:
            return nice
    return 1000


def draw_ruler(im: Image.Image, scale: float, step: int) -> Image.Image:
    """刻度标签 = 本视图左上角起算的原图像素。"""
    d = ImageDraw.Draw(im)
    cw, ch = im.size
    for v in range(0, int(cw / max(scale, 1e-6)) + 1, step):
        x = int(round(v * scale))
        if 0 <= x < cw:
            d.line([(x, 0), (x, ch)], fill=(255, 0, 0), width=1)
            d.rectangle([x + 1, 1, x + 8 + 6 * len(str(v)), 13], fill=(255, 255, 255))
            d.text((x + 2, 2), str(v), fill=(200, 0, 0))
    for v in range(0, int(ch / max(scale, 1e-6)) + 1, step):
        y = int(round(v * scale))
        if 0 <= y < ch:
            d.line([(0, y), (cw, y)], fill=(0, 100, 255), width=1)
            d.rectangle([1, y + 1, 8 + 6 * len(str(v)), y + 13], fill=(255, 255, 255))
            d.text((2, y + 2), str(v), fill=(0, 60, 200))
    return im


def dashed_rect(d, x0, y0, x1, y1, color, width=3, dash=14, gap=9):
    """粗虚线 + 白色衬底：与细实线刻度区分，深色底上也看得见。"""
    for halo, w in ((True, width + 3), (False, width)):
        fill = (255, 255, 255) if halo else color
        for xa, ya, xb, yb in ((x0, y0, x1, y0), (x1, y0, x1, y1),
                               (x1, y1, x0, y1), (x0, y1, x0, y0)):
            length = max(abs(xb - xa), abs(yb - ya))
            if length == 0:
                continue
            ux, uy = (xb - xa) / length, (yb - ya) / length
            pos = 0.0
            while pos < length:
                end = min(pos + dash, length)
                d.line([(xa + ux * pos, ya + uy * pos), (xa + ux * end, ya + uy * end)],
                       fill=fill, width=w)
                pos = end + gap


def draw_boxes(im: Image.Image, boxes, origin, scale) -> Image.Image:
    """把页面像素框画在本视图上；标签里写出框自己的页面坐标。"""
    d = ImageDraw.Draw(im)
    cw, ch = im.size
    ox, oy = origin
    for k, (bx0, by0, bx1, by1) in enumerate(boxes, 1):
        pts = [int(round((bx0 - ox) * scale)), int(round((by0 - oy) * scale)),
               int(round((bx1 - ox) * scale)), int(round((by1 - oy) * scale))]
        col = BOX_COLORS[(k - 1) % len(BOX_COLORS)]
        dashed_rect(d, pts[0], pts[1], pts[2], pts[3], col)
        label = f"#{k} {int(bx0)},{int(by0)},{int(bx1)},{int(by1)}"
        tw = 12 + 6 * len(label)
        lx = min(max(0, pts[0]), max(0, cw - tw))
        ly = min(max(0, pts[1] - 18), max(0, ch - 17))
        d.rectangle([lx, ly, lx + tw, ly + 16], fill=(255, 255, 255), outline=col)
        d.text((lx + 3, ly + 3), label, fill=col)
    return im


# ---------------------------------------------------------------- view


def crop_page(doc: Doc, page: int, box):
    im = doc.page(page)
    W, H = im.size
    if box is None:
        return im.copy(), 0, 0
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, max(x0 + 1, x1)), min(H, max(y0 + 1, y1))
    return im.crop((x0, y0, x1, y1)), x0, y0


def cmd_view(a):
    doc = Doc(a.input)
    if a.box and a.pack:
        sys.exit("ruler: --box and --pack are mutually exclusive")
    if a.pack:
        return _view_pack(a, doc)
    if a.fit == "union" and not a.boxes:
        sys.exit("ruler: --fit union needs --boxes")

    if a.box:
        items = parse_items(a.box, a.page)
        if len(items) != 1:
            sys.exit("ruler: --box takes exactly one box")
        page, box = items[0]
    else:
        page, box = a.page, None
    if a.fit == "union":
        bs = [b for _, b in parse_items(a.boxes, page) if b is not None]
        if not bs:
            sys.exit("ruler: --fit union needs boxes in --boxes")
        box = (min(b[0] for b in bs), min(b[1] for b in bs),
               max(b[2] for b in bs), max(b[3] for b in bs))

    im, ox, oy = crop_page(doc, page, box)
    scale = a.scale
    if scale < 0:
        scale = max(1.0, min(6.0, a.target / max(1, im.width)))
    elif scale == 0:
        scale = 1.0
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                       Image.LANCZOS)
    step = a.step if a.step else auto_step(scale)
    if a.ruler:
        draw_ruler(im, scale, step)

    drawn = []
    if a.boxes:
        drawn = [b for p, b in parse_items(a.boxes, page) if b is not None and p == page]
        im = draw_boxes(im, drawn, (ox, oy), scale)
    im.save(a.out)
    print(json.dumps({"out": a.out, "page": page, "page_px": list(doc.page(page).size),
                      "frame": "local", "origin_px": [ox, oy], "scale": round(scale, 3),
                      "step": step, "view_px": list(im.size),
                      "boxes": [{"i": k + 1, "page": page, "box_px": [round(v, 1) for v in b]}
                                for k, b in enumerate(drawn)],
                      "note": "刻度从本视图左上角起算；用 norm --origin 换算回整页"},
                     ensure_ascii=False))


def _view_pack(a, doc):
    items = parse_items(a.pack, a.page)
    tiles = []
    for page, box in items:
        crop, ox, oy = crop_page(doc, page, box)
        tiles.append({"crop": crop, "page": page, "origin": (ox, oy), "size": crop.size})
    big = max(max(t["size"][0] for t in tiles), max(t["size"][1] for t in tiles))
    scale = a.scale if a.scale > 0 else max(1.0, min(4.0, a.target / max(1, big)))
    for t in tiles:
        if scale != 1.0:
            t["crop"] = t["crop"].resize((max(1, int(t["crop"].width * scale)),
                                          max(1, int(t["crop"].height * scale))), Image.LANCZOS)
        t["step"] = a.step if a.step else auto_step(scale)
        draw_ruler(t["crop"], scale, t["step"])

    pad, head, n = a.gap, 36, len(tiles)

    def layout(ncols):
        rows = [tiles[i:i + ncols] for i in range(0, n, ncols)]
        rw = [sum(t["crop"].width for t in r) + pad * (len(r) - 1) for r in rows]
        rh = [max(t["crop"].height for t in r) + head for r in rows]
        W, H = max(rw) + 2 * pad, sum(rh) + pad * (len(rows) + 1)
        content = sum(t["crop"].width * t["crop"].height for t in tiles)
        return rows, W, H, W * H - content

    best = None
    for ncols in range(1, n + 1):
        rows, W, H, waste = layout(ncols)
        if W <= a.max_width and (best is None or waste < best[3]):
            best = (rows, W, H, waste)
    rows, W, H, _ = best if best else layout(1)

    canvas = Image.new("RGB", (W, H), (232, 232, 236))
    d = ImageDraw.Draw(canvas)
    meta, k, y = [], 0, pad
    for row in rows:
        x = pad
        for t in row:
            k += 1
            crop = t["crop"]
            d.rectangle([x - 3, y - 3, x + crop.width + 2, y + head + crop.height + 2],
                        fill=(255, 255, 255), outline=(120, 120, 128), width=2)
            d.text((x + 4, y + 9), f"T{k}", fill=(20, 20, 20))
            d.text((x + 46, y + 9),
                   f"p{t['page']} · {t['size'][0]}x{t['size'][1]}px · scale {scale:.2f}",
                   fill=(110, 110, 118))
            canvas.paste(crop, (x, y + head))
            meta.append({"tile": k, "page": t["page"], "origin_px": list(t["origin"]),
                         "local_px": list(t["size"]), "scale": round(scale, 3), "step": t["step"]})
            x += crop.width + pad
        y += max(t["crop"].height for t in row) + head + pad
    canvas.save(a.out)
    if a.map:
        with open(a.map, "w", encoding="utf-8") as fh:
            json.dump({"scale": round(scale, 3), "tiles": meta}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"out": a.out, "frame": "local", "tiles": meta, "map": a.map or None,
                      "note": "每块左上角是 0,0；用 norm --map 换算回各自的页"},
                     ensure_ascii=False))


# ---------------------------------------------------------------- norm


def cmd_norm(a):
    doc = Doc(a.input)
    tiles = {}
    if a.map:
        with open(a.map, "r", encoding="utf-8") as fh:
            for t in json.load(fh)["tiles"]:
                tiles[str(t["tile"])] = t
    ox0, oy0 = (float(v) for v in a.origin.split(",")) if a.origin else (0.0, 0.0)
    out = []
    for raw in a.boxes.split(";"):
        part = raw.strip()
        if not part:
            continue
        page, ox, oy = a.page, ox0, oy0
        if tiles:
            tid, _, body = part.partition(":")
            t = tiles.get(tid.strip())
            if t is None:
                sys.exit(f"ruler: no tile {tid!r} in map")
            page, ox, oy = t["page"], t["origin_px"][0], t["origin_px"][1]
            part = body.strip()
        vals = [float(v) for v in part.replace(" ", "").split(",")]
        if len(vals) != 4:
            sys.exit(f"ruler: box needs 4 numbers, got {part!r}")
        W, H = doc.page(page).size
        x0, y0 = vals[0] + ox, vals[1] + oy
        x1, y1 = vals[2] + ox, vals[3] + oy
        out.append({"page": page,
                    "box": [round(max(0.0, min(1.0, x0 / W)), 4), round(max(0.0, min(1.0, y0 / H)), 4),
                            round(max(0.0, min(1.0, x1 / W)), 4), round(max(0.0, min(1.0, y1 / H)), 4)]})
    print(json.dumps({"boxes": out}, ensure_ascii=False))


# ---------------------------------------------------------------- CLI


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ruler")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("view", help="取图 / 刻度 / 画框 / 打包，都是开关，可组合")
    v.add_argument("input", help="PDF 或页面图片")
    v.add_argument("--out", required=True, help="输出图片路径")
    v.add_argument("--page", type=int, default=1, help="默认页号，从 1 开始；条目没写页号时用它")
    v.add_argument("--box", default="", help="取一块：x0,y0,x1,y1，可写页号前缀 3:x0,y0,x1,y1")
    v.add_argument("--scale", type=float, default=0,
                   help="缩放；0=不放大，负数=按 --target 放大")
    v.add_argument("--target", type=int, default=1200, help="自动缩放的目标宽度（像素）")
    v.add_argument("--ruler", action="store_true", help="叠加刻度")
    v.add_argument("--step", type=int, default=0, help="刻度间隔（原图像素）；0=自动")
    v.add_argument("--boxes", default="",
                   help="画框：整页像素，分号分隔，可写页号前缀；标签里写出各自的页面坐标")
    v.add_argument("--fit", choices=["none", "union"], default="none",
                   help="union：先裁到 --boxes 的并集再画")
    v.add_argument("--pack", default="",
                   help="打包多块：每项 x0,y0,x1,y1；只写页号表示整页；分号分隔")
    v.add_argument("--gap", type=int, default=26, help="打包时块间缝隙（像素）")
    v.add_argument("--max-width", type=int, default=2000, help="打包画布最大宽度")
    v.add_argument("--map", default="", help="打包时写出各块的页号/原点，供 norm --map 使用")

    n = sub.add_parser("norm", help="把本视图的读数换算成整页归一化坐标 0..1")
    n.add_argument("input")
    n.add_argument("--boxes", required=True,
                   help="本视图坐标，分号分隔；用 --map 时写成 块号:x0,y0,x1,y1")
    n.add_argument("--origin", default="", help="本视图左上角的整页像素 X,Y")
    n.add_argument("--map", default="", help="view --pack 产出的 map.json")
    n.add_argument("--page", type=int, default=1, help="默认页号；用 --map 时按块自带的页号")

    a = ap.parse_args(argv)
    {"view": cmd_view, "norm": cmd_norm}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
