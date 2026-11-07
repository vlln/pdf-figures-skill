#!/usr/bin/env python3
"""pdfx — 依赖极小的 PDF 取图定位工具。

设计约束（不要破坏）：
  1. 只依赖两个 wheel：pypdfium2（唯一用途：页 -> 位图）与 Pillow。
  2. 不解析 PDF 结构：没有文本层、没有矢量图元、没有 xref 语义。
     全流程只吃像素，因此矢量页与纯位图扫描页走同一条路径。
  3. 不使用 numpy：投影用 Pillow 的一维 BOX 缩放实现。

坐标约定：归一化 0..1，左上原点，相对于「渲染后的整页」。
所有子命令输出一行 JSON 到 stdout，大图只写文件、只回路径。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageOps

# ---------------------------------------------------------------- 渲染层（唯一依赖点）


def _pdfium():
    try:
        import pypdfium2
    except ImportError:  # pragma: no cover
        sys.exit("pdfx: pypdfium2 missing -> pip install pypdfium2 pillow")
    return pypdfium2



def image_to_pdf(img_path: str, pdf_path: str) -> tuple[int, int]:
    """把一个页面图包成单页 PDF。评测集只有页面图，而渲染层只认 PDF，这里做桥梁。"""
    from PIL import Image
    import io

    with Image.open(img_path) as im:
        w, h = im.size
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=92)
    jpeg = buf.getvalue()
    pw, ph = float(w), float(h)
    content = f"q {pw} 0 0 {ph} 0 0 cm /Im0 Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + f"{pw:.2f} {ph:.2f}".encode()
         + b"] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        (b"<< /Type /XObject /Subtype /Image /Width " + str(w).encode()
         + b" /Height " + str(h).encode()
         + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
         + str(len(jpeg)).encode() + b" >>\nstream\n" + jpeg + b"\nendstream"),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    with open(pdf_path, "wb") as fh:
        fh.write(out)
    return w, h


class Doc:
    """把 PDF 当作「页的序列」，每页只能被光栅化，别的一概不知道。"""

    def __init__(self, path: str):
        self.path = path
        self.source = path
        with open(path, "rb") as fh:
            head = fh.read(5)
        self.is_image = head != b"%PDF-"
        if self.is_image:                         # 页面图：先包成单页 PDF，再走同一条路径
            import tempfile
            tmp = os.path.join(tempfile.mkdtemp(prefix="pdfx-"), "page.pdf")
            image_to_pdf(path, tmp)
            self.path = tmp
        self._doc = _pdfium().PdfDocument(self.path)

    def __len__(self) -> int:
        return len(self._doc)

    def size_pt(self, i: int) -> tuple[float, float]:
        w, h = self._doc[i].get_size()
        return float(w), float(h)

    def render(self, i: int, dpi: int, box=None) -> Image.Image:
        pg = self._doc[i]
        w, h = pg.get_size()
        scale = dpi / 72.0
        if box is None:
            bm = pg.render(scale=scale)
        else:
            x0, y0, x1, y1 = box
            # pypdfium2 的 crop 是四边内缩量（point），实测顺序是 left/bottom/right/top。
            # 竖排两项目标相反很容易写反：写反后裁出来是一片空白，自检会抓到。
            bm = pg.render(scale=scale, crop=(x0 * w, (1.0 - y1) * h, (1.0 - x1) * w, y0 * h))
        return bm.to_pil().convert("RGB")


# ---------------------------------------------------------------- 像素算子（纯 PIL）


def background(im: Image.Image) -> int:
    """页面背景亮度：直方图众数。扫描件不会是 255，这是唯一的自适应来源。"""
    hist = im.convert("L").histogram()
    return max(range(256), key=lambda v: hist[v])


def normalize(im: Image.Image) -> Image.Image:
    """暗底页面（照片、反色扫描）先反相，否则阈值会把整页判成墨迹。"""
    g = im.convert("L")
    return ImageOps.invert(g) if background(g) < 128 else g


def page_threshold(im: Image.Image) -> int:
    g = normalize(im)
    return threshold_for(background(g))


def threshold_for(bg: int) -> int:
    """相对背景取阈值。固定 245 在有噪点的扫描页上会把整页判成墨迹（实测）。"""
    return max(150, min(245, bg - 18))


def ink(im: Image.Image, th: int | None = None) -> Image.Image:
    """二值墨迹掩膜：255=有墨，0=空白。th 缺省时按本页自适应。"""
    g = normalize(im)
    if th is None:
        th = threshold_for(background(g))
    return g.point(lambda v: 255 if v < th else 0)


def trim(im: Image.Image, th: int, pad: int = 0):
    """墨迹包围盒。返回 (bbox, 是否触边)。触边 = 外框本身切了内容。"""
    mask = ink(im, th)
    bb = mask.getbbox()
    if bb is None:
        return None, {}
    W, H = im.size
    touch = {
        "left": bb[0] <= 1,
        "top": bb[1] <= 1,
        "right": bb[2] >= W - 1,
        "bottom": bb[3] >= H - 1,
    }
    if pad:
        bb = (max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad))
    return bb, touch


def profile(mask: Image.Image, axis: str) -> list[int]:
    """投影。axis='row' -> 每行墨量；'col' -> 每列墨量。不依赖 numpy。"""
    W, H = mask.size
    small = mask.resize((1, H), Image.BOX) if axis == "row" else mask.resize((W, 1), Image.BOX)
    return list(small.getdata())


def runs(prof: list[int], min_run: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(prof):
        if v > 0 and start is None:
            start = i
        elif v == 0 and start is not None:
            if i - start >= min_run:
                out.append((start, i))
            start = None
    if start is not None and len(prof) - start >= min_run:
        out.append((start, len(prof)))
    return out


def gaps(prof: list[int], min_run: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(prof):
        if v == 0:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= min_run:
                out.append((start, i))
            start = None
    if start is not None and len(prof) - start >= min_run:
        out.append((start, len(prof)))
    return out


# ---------------------------------------------------------------- 叠层（给模型看的辅助图）


def overlay_grid(im: Image.Image, step: float = 0.05, major: float = 0.1) -> Image.Image:
    im = im.copy()
    W, H = im.size
    d = ImageDraw.Draw(im)
    n = int(round(1 / step))
    for k in range(n + 1):
        v = k * step
        x, y = min(W - 1, int(v * W)), min(H - 1, int(v * H))
        is_major = abs(v / major - round(v / major)) < 1e-6
        col = (255, 60, 60) if is_major else (255, 185, 185)
        d.line([(x, 0), (x, H)], fill=col, width=2 if is_major else 1)
        d.line([(0, y), (W, y)], fill=col, width=2 if is_major else 1)
        if is_major and k < n:
            d.text((x + 4, 3), f"{v:.1f}", fill=(190, 0, 0))
            d.text((3, y + 2), f"{v:.1f}", fill=(190, 0, 0))
    return im


def numbered_lines(im: Image.Image, min_run: int, color=(0, 150, 255)) -> tuple[Image.Image, list]:
    """把一段区域切成文本行并编号。模型只需回答「切到第几行」，不必给坐标。"""
    mask = ink(im)
    H = im.size[1]
    lines = runs(profile(mask, "row"), max(3, min_run))
    out = im.copy()
    d = ImageDraw.Draw(out)
    for k, (s, _e) in enumerate(lines):
        d.line([(0, s), (im.size[0], s)], fill=color, width=1)
        d.text((3, s + 1), f"L{k + 1}", fill=(0, 90, 200))
    return out, lines


# ---------------------------------------------------------------- 子命令


def cmd_probe(a):
    with open(a.pdf, "rb") as fh:
        a.pdf_path_is_pdf = fh.read(5) == b"%PDF-"
    doc = Doc(a.pdf)
    pages = []
    for i in range(len(doc)):
        im = doc.render(i, a.dpi)
        bg = background(im)
        hist = im.convert("L").histogram()
        white = sum(hist[252:]) / float(im.size[0] * im.size[1])
        pages.append(
            {
                "page": i + 1,
                "size_pt": [round(v, 2) for v in doc.size_pt(i)],
                "background": bg,
                "threshold": threshold_for(bg),
                # kind 说的是输入形态，不是页面内容：image 输入没有更高分辨率可重画。
                # noisy 用于判断阈值是否可靠（背景不是纯白 = 扫描/照片/压缩痕迹）。
                "kind": "image" if not a.pdf_path_is_pdf else "pdf",
                "noisy": bg < 250,
                "white_ratio": round(white, 3),
            }
        )
    print(json.dumps({"path": a.pdf, "pages": len(doc), "probe": pages}, ensure_ascii=False))


def cmd_page(a):
    doc = Doc(a.pdf)
    im = doc.render(a.n - 1, a.dpi)
    if a.grid:
        im = overlay_grid(im)
    im.save(a.out)
    print(json.dumps({"page": a.n, "out": a.out, "px": list(im.size), "dpi": a.dpi}))


def cmd_sheet(a):
    doc = Doc(a.pdf)
    ims = [doc.render(i, a.dpi) for i in range(len(doc))]
    w, h = ims[0].size
    pad, lab = 8, 16
    cols = a.cols
    rows = (len(ims) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + pad + lab) + pad), (235, 235, 235))
    d = ImageDraw.Draw(canvas)
    for k, im in enumerate(ims):
        r, c = divmod(k, cols)
        x, y = pad + c * (w + pad), pad + r * (h + pad + lab)
        canvas.paste(im, (x, y))
        d.text((x + 2, y + h + 2), f"p{k + 1}", fill=(0, 0, 0))
        d.rectangle([x - 1, y - 1, x + w, y + h], outline=(120, 120, 120))
    canvas.save(a.out)
    print(json.dumps({"pages": len(ims), "out": a.out, "px": list(canvas.size), "cols": cols}))


def cmd_strip(a):
    doc = Doc(a.pdf)
    im = doc.render(a.n - 1, a.dpi, (a.x0, a.y0, a.x1, a.y1))
    out, lines = numbered_lines(im, a.dpi // 30)
    out.save(a.out)
    H = im.size[1]
    span = a.y1 - a.y0
    norm = [
        {"i": k + 1, "y": round(a.y0 + s / H * span, 4), "y_end": round(a.y0 + e / H * span, 4)}
        for k, (s, e) in enumerate(lines)
    ]
    print(json.dumps({"page": a.n, "lines": norm, "out": a.out}, ensure_ascii=False))


def _box_norm(pg_box, im_size, bb):
    x0, y0, x1, y1 = pg_box
    W, H = im_size
    sx, sy = (x1 - x0) / W, (y1 - y0) / H
    return [round(x0 + bb[0] * sx, 4), round(y0 + bb[1] * sy, 4),
            round(x0 + bb[2] * sx, 4), round(y0 + bb[3] * sy, 4)]


def cmd_crop(a):
    doc = Doc(a.pdf)
    box = tuple(float(v) for v in a.box.split(","))
    im = doc.render(a.n - 1, a.dpi, box)
    th = page_threshold(doc.render(a.n - 1, 72))
    bb, touch = trim(im, th)
    info = {"page": a.n, "box": list(box), "px": list(im.size), "dpi": a.dpi, "threshold": th}
    if bb:
        info["ink_norm"] = _box_norm(box, im.size, bb)
        info["touch_edge"] = touch

    if (a.tight or a.split) and bb:
        mask = ink(im, th)
        H, W = im.size[1], im.size[0]
        if a.split:
            rg = [(s / H, e / H) for s, e in gaps(profile(mask, "row"), max(4, H // 250)) if e - s > H * 0.03]
            cg = [(s / W, e / W) for s, e in gaps(profile(mask, "col"), max(4, W // 250)) if e - s > W * 0.06]
            info["row_gaps"] = [[round(s, 3), round(e, 3)] for s, e in rg]
            info["col_gaps"] = [[round(s, 3), round(e, 3)] for s, e in cg]
        if a.tight:
            pad = max(4, H // 150)
            im = im.crop((max(0, bb[0] - pad), max(0, bb[1] - pad),
                          min(W, bb[2] + pad), min(H, bb[3] + pad)))
            info["tight_px"] = list(im.size)

    im.save(a.out)
    info["out"] = a.out
    print(json.dumps(info, ensure_ascii=False))



def cmd_export(a):
    """批量导出：读一份 spec（每项 页号 + 归一化框），落盘图片 + manifest。

    spec 形如 {"items":[{"page":3,"box":[x0,y0,x1,y1],"label":"Figure 1"}]}
    """
    import json as _json
    import os as _os

    doc = Doc(a.pdf)
    with open(a.spec, "r", encoding="utf-8") as fh:
        spec = _json.load(fh)
    items = spec.get("items") or []
    fig_dir = _os.path.join(a.out, "figures")
    _os.makedirs(fig_dir, exist_ok=True)

    th_cache = {}
    records = []
    for k, it in enumerate(items, 1):
        page = int(it["page"])
        box = [float(v) for v in it["box"]]
        if page not in th_cache:
            th_cache[page] = page_threshold(doc.render(page - 1, 72))
        im = doc.render(page - 1, a.dpi, tuple(box))
        bb, touch = trim(im, th_cache[page])
        if a.tight and bb:
            pad = max(4, im.size[1] // 150)
            im = im.crop((max(0, bb[0] - pad), max(0, bb[1] - pad),
                          min(im.size[0], bb[2] + pad), min(im.size[1], bb[3] + pad)))
        name = f"p{page:03d}_fig{k:02d}.png"
        im.save(_os.path.join(fig_dir, name))
        records.append({"file": f"figures/{name}", "page": page, "box": box,
                        "px": list(im.size), "dpi": a.dpi,
                        "label": it.get("label"), "touch_edge": touch})

    manifest = {"source": _os.path.abspath(a.pdf), "dpi": a.dpi,
                "items": records, "count": len(records)}
    with open(_os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as fh:
        _json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"out": a.out, "count": len(records),
                      "files": [r["file"] for r in records]}, ensure_ascii=False))



def _bands(total, spans):
    """把若干 (start,end) 覆盖区间取反，得到未覆盖的连续段。"""
    out, cur = [], 0
    for a, b in sorted(spans):
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        out.append((cur, total))
    return out




def scan_blocks(doc, page_index=0, dpi=150, max_blocks=40):
    """页面 -> 候选块（bbox + 面积 + 墨迹密度）。cmd_scan 与 M1 runner 共用。"""
    im = doc.render(page_index, dpi)
    th = page_threshold(doc.render(page_index, 72))
    mask = ink(im, th)
    W, H = im.size
    row_prof = profile(mask, "row")
    spans = []
    for s0, e0 in runs(row_prof, 2):
        if spans and s0 - spans[-1][1] < H * 0.004:
            spans[-1] = (spans[-1][0], e0)
        else:
            spans.append((s0, e0))
    blocks = []
    for y0, y1 in spans:
        if y1 - y0 < H * 0.02:
            continue
        for x0, x1 in runs(profile(mask.crop((0, y0, W, y1)), "col"), 2):
            if x1 - x0 < W * 0.04:
                continue
            sub = mask.crop((x0, y0, x1, y1))
            sw, sh = max(2, (x1 - x0) // 10), max(2, (y1 - y0) // 10)
            vals = list(sub.resize((sw, sh), 1).getdata())
            dens = sum(1 for v in vals if v > 60) / max(1, len(vals))
            area = ((x1 - x0) / W) * ((y1 - y0) / H)
            if area < 0.004:
                continue
            blocks.append({"i": len(blocks) + 1,
                           "box": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)],
                           "area": round(area, 3), "ink": round(dens, 3)})
    blocks.sort(key=lambda b: -b["area"])
    blocks = blocks[:max_blocks]
    for k, b in enumerate(blocks, 1):
        b["i"] = k
    return blocks, (W, H)


def render_montage(doc, blocks, path, tile_dpi=180, tile_width=420, page_index=0):
    """把候选块渲染成带 #编号 的竖排拼图。"""
    tiles = []
    for b in blocks:
        sub = doc.render(page_index, tile_dpi, tuple(b["box"]))
        tw = tile_width
        th_ = max(1, int(sub.size[1] * tw / sub.size[0]))
        sub = sub.resize((tw, th_), Image.LANCZOS)
        tile = Image.new("RGB", (tw + 8, th_ + 26), (245, 245, 245))
        tile.paste(sub, (4, 22))
        ImageDraw.Draw(tile).text((6, 4), f"#{b['i']}", fill=(180, 0, 0))
        tiles.append(tile)
    if not tiles:
        return None
    canvas = Image.new("RGB", (tiles[0].size[0], sum(t.size[1] for t in tiles)), (255, 255, 255))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y))
        y += t.size[1]
    canvas.save(path)
    return path



def _ruler(im, x0, y0, scale, step, W, H):
    """在裁剪图上按**原图像素坐标**画刻度并标数值（红色= x，蓝色= y）。

    这是从一次自由 run 里学来的做法：标签用原图像素值，不随缩放改变，
    所以模型在任意放大级别读到的数字都落在同一个坐标系里。
    """
    d = ImageDraw.Draw(im)
    cw, ch = im.size
    xs = [x for x in range(0, W, step) if x0 <= x <= x0 + cw / max(scale, 1e-6)]
    ys = [y for y in range(0, H, step) if y0 <= y <= y0 + ch / max(scale, 1e-6)]
    for x in xs:
        xx = int((x - x0) * scale)
        if 0 <= xx < cw:
            d.line([(xx, 0), (xx, ch)], fill=(255, 0, 0), width=1)
            d.rectangle([xx + 1, 1, xx + 7 + 6 * len(str(x)), 13], fill=(255, 255, 255))
            d.text((xx + 2, 2), str(x), fill=(200, 0, 0))
    for y in ys:
        yy = int((y - y0) * scale)
        if 0 <= yy < ch:
            d.line([(0, yy), (cw, yy)], fill=(0, 100, 255), width=1)
            d.rectangle([1, yy + 1, 6 + 6 * len(str(y)), yy + 13], fill=(255, 255, 255))
            d.text((2, yy + 2), str(y), fill=(0, 60, 200))
    return im


def cmd_ruler_page(a):
    """整页 + 像素刻度（红 x / 蓝 y），标签是原图像素值。"""
    doc = Doc(a.pdf)
    im = doc.render(a.n - 1, a.dpi)
    _ruler(im, 0, 0, 1.0, a.step, im.size[0], im.size[1])
    im.save(a.out)
    print(json.dumps({"page": a.n, "out": a.out, "px": list(im.size), "dpi": a.dpi,
                      "note": "刻度标签是像素坐标；最终框用像素给，脚本负责归一化"},
                     ensure_ascii=False))


def cmd_zoom(a):
    """按像素框裁剪 + 放大 + 叠原像素刻度。

    模型只需要说「放大这块」，读数字，再给下一层的像素框；坐标系始终是同一个。
    """
    doc = Doc(a.pdf)
    x0, y0, x1, y1 = [int(round(float(v))) for v in a.box.split(",")]
    full = doc.render(a.n - 1, a.dpi)
    W, H = full.size
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    crop = full.crop((x0, y0, x1, y1))
    if a.scale and a.scale != 1.0:
        crop = crop.resize((int(crop.width * a.scale), int(crop.height * a.scale)), Image.LANCZOS)
    _ruler(crop, x0, y0, a.scale, a.step, W, H)
    crop.save(a.out)
    print(json.dumps({"page": a.n, "box_px": [x0, y0, x1, y1], "scale": a.scale,
                      "out": a.out, "px": list(crop.size),
                      "norm": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)]},
                     ensure_ascii=False))



def cmd_prep(a):
    """一条命令把整批页面渲染成「带像素刻度」的图，供模型一眼过。

    真实任务里刻度必须有人画：这就是那一步。批量做，成本按页摊薄。
    """
    import glob as _glob
    srcs = []
    if os.path.isdir(a.src):
        for pat in ("*.png", "*.jpg", "*.jpeg", "*.JPG", "*.PNG"):
            srcs.extend(_glob.glob(os.path.join(a.src, pat)))
    else:
        srcs = [a.src]
    srcs.sort()
    os.makedirs(a.out, exist_ok=True)
    t0 = __import__("time").time()
    written = []
    for src in srcs:
        doc = Doc(src)
        im = doc.render(a.page - 1, a.dpi)
        _ruler(im, 0, 0, 1.0, a.step, im.size[0], im.size[1])
        name = os.path.splitext(os.path.basename(src))[0] + ".png"
        im.save(os.path.join(a.out, name))
        written.append(name)
    dt = __import__("time").time() - t0
    print(json.dumps({"prepared": len(written), "out": a.out, "dpi": a.dpi, "step": a.step,
                      "sec_total": round(dt, 2),
                      "sec_per_page": round(dt / max(1, len(written)), 3),
                      "files": written[:5]}, ensure_ascii=False))


def cmd_scan(a):
    """一次调用给全「这一页有什么可框的东西」：行带、候选块、可选编号拼图。"""
    doc = Doc(a.pdf)
    n = a.n - 1
    blocks, (W, H) = scan_blocks(doc, n, a.dpi, a.max_blocks)
    im = doc.render(n, a.dpi)
    mask = ink(im, page_threshold(doc.render(n, 72)))
    row_prof = profile(mask, "row")
    spans = []
    for s0, e0 in runs(row_prof, 2):
        if spans and s0 - spans[-1][1] < H * 0.004:
            spans[-1] = (spans[-1][0], e0)
        else:
            spans.append((s0, e0))
    bands = [{"y": [round(y0 / H, 4), round(y1 / H, 4)],
              "ink": round(sum(1 for v in row_prof[y0:y1] if v > 0) / max(1, y1 - y0), 3)}
             for y0, y1 in spans if y1 - y0 > H * 0.01]
    out = {"page": a.n, "px": [W, H], "bands": bands, "blocks": blocks,
           "blocks_total": len(blocks)}
    if a.montage and blocks:
        out["montage"] = render_montage(doc, blocks, a.montage, a.tile_dpi, a.tile_width, n)
    print(json.dumps(out, ensure_ascii=False))


def cmd_coverage(a):
    """给定已框区域，报告「还有多少墨迹没被覆盖」以及最大的几块空白。"""
    doc = Doc(a.pdf)
    n = a.n - 1
    im = doc.render(n, a.dpi)
    th = page_threshold(doc.render(n, 72))
    mask = ink(im, th)
    W, H = im.size
    total = sum(1 for v in mask.resize((64, 64), 1).getdata() if v > 60)
    d = ImageDraw.Draw(mask)
    prior = a.prior
    if isinstance(prior, str):
        try:
            prior = json.loads(prior)
        except json.JSONDecodeError:
            with open(prior, "r", encoding="utf-8") as fh:
                prior = json.load(fh)
    for b in prior:
        d.rectangle([b[0] * W, b[1] * H, b[2] * W, b[3] * H], fill=0)
    left = sum(1 for v in mask.resize((64, 64), 1).getdata() if v > 60)
    worst = []
    for y0, y1 in spans_of(mask, H):
        for x0, x1 in runs(profile(mask.crop((0, y0, W, y1)), "col"), 2):
            area = ((x1 - x0) / W) * ((y1 - y0) / H)
            if area >= a.min_gap:
                worst.append({"box": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)],
                              "area": round(area, 3)})
    worst.sort(key=lambda r: -r["area"])
    print(json.dumps({"page": a.n,
                      "uncovered_ink_ratio": round(left / max(1, total), 3),
                      "gaps": worst[: a.max_gaps]}, ensure_ascii=False))


def spans_of(mask, H):
    prof = profile(mask, "row")
    out = []
    for s0, e0 in runs(prof, 2):
        if out and s0 - out[-1][1] < H * 0.004:
            out[-1] = (out[-1][0], e0)
        else:
            out.append((s0, e0))
    return out


def cmd_leftover(a):
    """第二遍的强制入口：把「已有框之外还有墨迹的地方」切成带编号的候选块。

    模型不做自觉的复查，只做选择：看这张拼图，回答哪几个编号是图或表。
    """
    doc = Doc(a.pdf)
    n = a.n - 1
    im = doc.render(n, a.dpi)
    th = page_threshold(doc.render(n, 72))
    mask = ink(im, th)
    W, H = im.size

    # 抹掉已覆盖区域（略放宽，避免边界残留）
    pad = int(0.006 * min(W, H))
    prior = a.prior
    if isinstance(prior, str):
        try:
            prior = json.loads(prior)
        except json.JSONDecodeError:
            with open(prior, "r", encoding="utf-8") as fh:
                prior = json.load(fh)
    d = ImageDraw.Draw(mask)
    for b in prior:
        d.rectangle([b[0] * W - pad, b[1] * H - pad, b[2] * W + pad, b[3] * H + pad], fill=0)

    # 行带：空白间隔 >= 0.8% 页高 才切
    row_prof = profile(mask, "row")
    spans = []
    for s, e in runs(row_prof, 2):
        if spans and s - spans[-1][1] < H * 0.008:
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))

    cands = []
    for (y0, y1) in spans:
        if y1 - y0 < H * 0.02:
            continue
        band = mask.crop((0, y0, W, y1))
        col_prof = profile(band, "col")
        for (x0, x1) in runs(col_prof, 2):
            if x1 - x0 < W * 0.04:
                continue
            area = ((x1 - x0) / W) * ((y1 - y0) / H)
            if area < 0.003:
                continue
            cands.append([x0 / W, y0 / H, x1 / W, y1 / H])
    # 「大且密」预筛：真图表通常是面积大、内部墨迹连续的块；正文段落面积小、稀疏。
    # 实测（20 页 70 个候选）把候选池精度从 0.09 提到 1.00，代价是丢掉约六分之一的正例。
    kept = []
    for b in cands:
        area = (b[2] - b[0]) * (b[3] - b[1])
        if area < a.min_area:
            continue
        x0, y0 = int(b[0] * W), int(b[1] * H)
        x1, y1 = max(x0 + 1, int(b[2] * W)), max(y0 + 1, int(b[3] * H))
        sub = mask.crop((x0, y0, x1, y1)).resize((max(2, (x1 - x0) // 10), max(2, (y1 - y0) // 10)), 1)
        vals = list(sub.getdata())
        dens = sum(1 for v in vals if v > 60) / max(1, len(vals))
        if dens < a.min_ink:
            continue
        kept.append(b)
    cands = kept
    cands.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    cands = cands[: a.max_candidates]

    # 拼图：编号 + 局部放大，模型只需做选择题
    tiles = []
    for k, b in enumerate(cands, 1):
        sub = doc.render(n, a.tile_dpi, tuple(b))
        tw = a.tile_width
        th_ = max(1, int(sub.size[1] * tw / sub.size[0]))
        sub = sub.resize((tw, th_), Image.LANCZOS)
        tile = Image.new("RGB", (tw + 8, th_ + 26), (245, 245, 245))
        tile.paste(sub, (4, 22))
        ImageDraw.Draw(tile).text((6, 4), f"#{k}", fill=(180, 0, 0))
        tiles.append(tile)
    if tiles:
        montage = Image.new("RGB", (tiles[0].size[0], sum(t.size[1] for t in tiles)), (255, 255, 255))
        y = 0
        for t in tiles:
            montage.paste(t, (0, y))
            y += t.size[1]
        montage.save(a.out)

    print(json.dumps({"page": a.n, "candidates": [{"i": k + 1, "box": [round(v, 4) for v in b]}
                                                   for k, b in enumerate(cands)],
                      "montage": a.out if tiles else None}, ensure_ascii=False))


def cmd_split(a):
    doc = Doc(a.pdf)
    box = tuple(float(v) for v in a.box.split(","))
    im = doc.render(a.n - 1, 200, box)
    th = page_threshold(doc.render(a.n - 1, 72))
    mask = ink(im, th)
    W, H = im.size
    rg = [g for g in gaps(profile(mask, "row"), max(4, H // 250)) if g[1] - g[0] > H * 0.03]
    cg = [g for g in gaps(profile(mask, "col"), max(4, W // 250)) if g[1] - g[0] > W * 0.06]
    rows = _bands(H, rg)
    cols = _bands(W, cg)
    panels = []
    k = 0
    for (ya, yb) in rows:
        for (xa, xb) in cols:
            k += 1
            panels.append({
                "i": k,
                "box": [round(box[0] + xa / W * (box[2] - box[0]), 4), round(box[1] + ya / H * (box[3] - box[1]), 4),
                        round(box[0] + xb / W * (box[2] - box[0]), 4), round(box[1] + yb / H * (box[3] - box[1]), 4)],
            })
    print(json.dumps({"page": a.n, "rows": len(rows), "cols": len(cols), "panels": panels}, ensure_ascii=False))


def _bands(total: int, gs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    edges, cur = [], 0
    for s, e in gs:
        if s > cur:
            edges.append((cur, s))
        cur = e
    if cur < total:
        edges.append((cur, total))
    return edges or [(0, total)]


def cmd_selftest(a):
    import make_fixtures as fx

    tmp = a.out_dir
    fx.build(tmp)
    failures = []
    t0 = __import__("time").time()

    doc = Doc(f"{tmp}/vector.pdf")
    p1_bg = background(doc.render(0, 72))
    assert p1_bg > 240, p1_bg
    im = doc.render(0, 150, (0.10, 0.08, 0.56, 0.26))
    th = page_threshold(doc.render(0, 72))
    bb, touch = trim(im, th)
    got = _box_norm((0.10, 0.08, 0.56, 0.26), im.size, bb)
    want = fx.VECTOR_CHART_NORM
    err = max(abs(g - w) for g, w in zip(got, want))
    if err > 0.012:
        failures.append(f"vector trim err {err:.3f} got={got} want={want}")
    if any(touch.values()):
        failures.append(f"vector trim touched edge {touch}")
    out = cmd_crop_direct(doc, (0.10, 0.08, 0.56, 0.26), 150, th)
    page_area = (got[2] - got[0]) * (got[3] - got[1])
    if page_area > 0.30:
        failures.append(f"vector crop covers {page_area:.2f} of page")

    scan = Doc(f"{tmp}/scan.pdf")
    sim = scan.render(0, 110)
    sth = threshold_for(background(sim))
    sbb, stouch = trim(sim, sth)
    area = ((sbb[2] - sbb[0]) / sim.size[0]) * ((sbb[3] - sbb[1]) / sim.size[1])
    if area > 0.55:
        failures.append(f"scan trim collapsed to full page (area={area:.2f}) - threshold not adaptive")
    dist = max(abs(sbb[0] / sim.size[0] - fx.SCAN_BLOCK_NORM[0]),
               abs(sbb[1] / sim.size[1] - fx.SCAN_BLOCK_NORM[1]))
    if dist > 0.06:
        failures.append(f"scan block mislocated (dx={dist:.3f})")

    # leftover：给空 prior 时，图表区必须出现在候选里
    out_lef = cmd_leftover_direct(doc, [], 120, 6, 200, 400)
    if not out_lef:
        failures.append("leftover returned no candidates")
    else:
        covered = any(abs(c[0] - want[0]) < 0.05 and abs(c[1] - want[1]) < 0.05 for c in out_lef)
        if not covered:
            failures.append(f"leftover missed the chart: {[[round(v,3) for v in c] for c in out_lef]}")

    strip_im = doc.render(0, 170, (0.05, 0.25, 0.95, 0.40))
    _, lines = numbered_lines(strip_im, 170 // 30)
    if len(lines) < 2:
        failures.append(f"strip found {len(lines)} lines, expected >=2")

    dt = __import__("time").time() - t0
    if "numpy" in sys.modules:
        failures.append("numpy got imported somewhere")

    print(json.dumps({"selftest": "ok" if not failures else "FAIL", "failures": failures,
                      "seconds": round(dt, 2)}, ensure_ascii=False))
    return 0 if not failures else 1


def cmd_leftover_direct(doc, prior, dpi, max_candidates, tile_dpi, tile_width):
    im = doc.render(0, dpi)
    th = page_threshold(doc.render(0, 72))
    mask = ink(im, th)
    W, H = im.size
    pad = int(0.006 * min(W, H))
    d = ImageDraw.Draw(mask)
    for b in prior:
        d.rectangle([b[0] * W - pad, b[1] * H - pad, b[2] * W + pad, b[3] * H + pad], fill=0)
    spans = []
    for s, e in runs(profile(mask, "row"), 2):
        if spans and s - spans[-1][1] < H * 0.008:
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))
    out = []
    for (y0, y1) in spans:
        if y1 - y0 < H * 0.02:
            continue
        for (x0, x1) in runs(profile(mask.crop((0, y0, W, y1)), "col"), 2):
            if x1 - x0 < W * 0.04:
                continue
            if ((x1 - x0) / W) * ((y1 - y0) / H) < 0.03:
                continue
            sub = mask.crop((x0, y0, x1, y1)).resize((max(2, (x1 - x0) // 10), max(2, (y1 - y0) // 10)), 1)
            vals = list(sub.getdata())
            if sum(1 for v in vals if v > 60) / max(1, len(vals)) < 0.45:
                continue
            out.append([x0 / W, y0 / H, x1 / W, y1 / H])
    out.sort(key=lambda b: -((b[2] - b[0]) * (b[3] - b[1])))
    return out[:max_candidates]


def cmd_crop_direct(doc, box, dpi, th):
    im = doc.render(0, dpi, box)
    bb, touch = trim(im, th)
    W, H = im.size
    return bb, {"area": ((bb[2] - bb[0]) / W) * ((bb[3] - bb[1]) / H), "touch": touch}


# ---------------------------------------------------------------- 取景层：view / norm
#
# 与 bench/pilot/skills/pdf-ruler 里被六轮评测用过的形态对齐：几何参数一律是
# 「本渲染 dpi 下的整页像素」，每个开关只做一件事，可以自由组合。
# 刻度标签是「本视图左上角起算」的原图像素；换算回整页归一化坐标用 norm。

VIEW_DPI = 200
_VIEW_COLORS = [(0, 150, 0), (200, 0, 200), (230, 180, 0), (0, 0, 0), (110, 0, 190)]
_view_cache: dict = {}


def _view_page(doc: "Doc", n: int) -> Image.Image:
    """view/norm 共用的页面位图：固定 200 dpi 渲染一次，坐标就以它为准。"""
    if getattr(doc, "is_image", False):
        # 图片输入直接按原生像素用，不再按 dpi 放大：坐标要和用户看到的图一致
        if n != 1:
            sys.exit("pdfx: image input has only page 1")
        key = ("image", doc.source)
        if key not in _view_cache:
            _view_cache[key] = Image.open(doc.source).convert("RGB")
        return _view_cache[key]
    if not 1 <= n <= len(doc):
        sys.exit(f"pdfx: page {n} out of range (1..{len(doc)})")
    key = (doc.path, n)
    if key not in _view_cache:
        _view_cache[key] = doc.render(n - 1, VIEW_DPI)
    return _view_cache[key]


def _parse_items(text: str, default_page: int) -> list[tuple[int, tuple | None]]:
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
                sys.exit(f"pdfx: bad page prefix in {part!r}")
            page, part = int(head), tail.strip()
        if part:
            try:
                vals = [float(v) for v in part.replace(" ", "").split(",")]
            except ValueError:
                sys.exit(f"pdfx: box must be 4 numbers, got {part!r}")
            if len(vals) != 4:
                sys.exit(f"pdfx: box needs 4 numbers, got {part!r}")
            box = tuple(vals)
        out.append((page, box))
    if not out:
        sys.exit("pdfx: empty item list")
    return out


def _auto_step(scale: float, target_px: int = 90) -> int:
    raw = max(1.0, target_px / max(scale, 1e-6))
    for nice in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if nice >= raw:
            return nice
    return 1000


def _draw_ruler_view(im: Image.Image, scale: float, step: int) -> Image.Image:
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


def _dashed_rect(d, x0, y0, x1, y1, color, width=3, dash=14, gap=9) -> None:
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


def _draw_boxes_view(im: Image.Image, boxes, origin, scale) -> Image.Image:
    """把整页像素框画到本视图上；标签写出框自己的整页坐标。"""
    d = ImageDraw.Draw(im)
    cw, ch = im.size
    ox, oy = origin
    for k, (bx0, by0, bx1, by1) in enumerate(boxes, 1):
        pts = [int(round((bx0 - ox) * scale)), int(round((by0 - oy) * scale)),
               int(round((bx1 - ox) * scale)), int(round((by1 - oy) * scale))]
        col = _VIEW_COLORS[(k - 1) % len(_VIEW_COLORS)]
        _dashed_rect(d, pts[0], pts[1], pts[2], pts[3], col)
        label = f"#{k} {int(bx0)},{int(by0)},{int(bx1)},{int(by1)}"
        tw = 12 + 6 * len(label)
        lx = min(max(0, pts[0]), max(0, cw - tw))
        ly = min(max(0, pts[1] - 18), max(0, ch - 17))
        d.rectangle([lx, ly, lx + tw, ly + 16], fill=(255, 255, 255), outline=col)
        d.text((lx + 3, ly + 3), label, fill=col)
    return im


def _crop_view(doc: "Doc", page: int, box):
    im = _view_page(doc, page)
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
        sys.exit("pdfx: --box and --pack are mutually exclusive")
    if a.pack:
        return _cmd_view_pack(a, doc)
    if a.fit == "union" and not a.boxes:
        sys.exit("pdfx: --fit union needs --boxes")

    if a.box:
        items = _parse_items(a.box, a.page)
        if len(items) != 1:
            sys.exit("pdfx: --box takes exactly one box")
        page, box = items[0]
    else:
        page, box = a.page, None
    if a.fit == "union":
        bs = [b for _, b in _parse_items(a.boxes, page) if b is not None]
        if not bs:
            sys.exit("pdfx: --fit union needs boxes in --boxes")
        box = (min(b[0] for b in bs), min(b[1] for b in bs),
               max(b[2] for b in bs), max(b[3] for b in bs))

    im, ox, oy = _crop_view(doc, page, box)
    scale = a.scale
    if scale < 0:
        scale = max(1.0, min(6.0, a.target / max(1, im.width)))
    elif scale == 0:
        scale = 1.0
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                       Image.LANCZOS)
    step = a.step if a.step else _auto_step(scale)
    if a.ruler:
        _draw_ruler_view(im, scale, step)

    drawn = []
    if a.boxes:
        drawn = [b for p, b in _parse_items(a.boxes, page) if b is not None and p == page]
        im = _draw_boxes_view(im, drawn, (ox, oy), scale)
    im.save(a.out)
    print(json.dumps({"out": a.out, "page": page, "page_px": list(_view_page(doc, page).size),
                      "frame": "local", "origin_px": [ox, oy], "scale": round(scale, 3),
                      "step": step, "view_px": list(im.size),
                      "boxes": [{"i": k + 1, "page": page, "box_px": [round(v, 1) for v in b]}
                                for k, b in enumerate(drawn)],
                      "note": "刻度从本视图左上角起算；用 norm --origin 换算回整页"},
                     ensure_ascii=False))


def _cmd_view_pack(a, doc):
    items = _parse_items(a.pack, a.page)
    tiles = []
    for page, box in items:
        crop, ox, oy = _crop_view(doc, page, box)
        tiles.append({"crop": crop, "page": page, "origin": (ox, oy), "size": crop.size})
    big = max(max(t["size"][0] for t in tiles), max(t["size"][1] for t in tiles))
    scale = a.scale if a.scale > 0 else max(1.0, min(4.0, a.target / max(1, big)))
    for t in tiles:
        if scale != 1.0:
            t["crop"] = t["crop"].resize((max(1, int(t["crop"].width * scale)),
                                          max(1, int(t["crop"].height * scale))), Image.LANCZOS)
        t["step"] = a.step if a.step else _auto_step(scale)
        _draw_ruler_view(t["crop"], scale, t["step"])

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
                sys.exit(f"pdfx: no tile {tid!r} in map")
            page, ox, oy = t["page"], t["origin_px"][0], t["origin_px"][1]
            part = body.strip()
        try:
            vals = [float(v) for v in part.replace(" ", "").split(",")]
        except ValueError:
            sys.exit(f"pdfx: box must be 4 numbers, got {part!r}")
        if len(vals) != 4:
            sys.exit(f"pdfx: box needs 4 numbers, got {part!r}")
        W, H = _view_page(doc, page).size
        x0, y0 = vals[0] + ox, vals[1] + oy
        x1, y1 = vals[2] + ox, vals[3] + oy
        out.append({"page": page,
                    "box": [round(max(0.0, min(1.0, x0 / W)), 4), round(max(0.0, min(1.0, y0 / H)), 4),
                            round(max(0.0, min(1.0, x1 / W)), 4), round(max(0.0, min(1.0, y1 / H)), 4)]})
    print(json.dumps({"boxes": out}, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pdfx", description="PDF figure locator (pixels only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("pdf"); p.add_argument("--dpi", type=int, default=72)
    p = sub.add_parser("page"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--dpi", type=int, default=110); p.add_argument("--grid", action="store_true")
    p.add_argument("--out", default="page.png")
    p = sub.add_parser("sheet"); p.add_argument("pdf"); p.add_argument("--dpi", type=int, default=45)
    p.add_argument("--cols", type=int, default=5); p.add_argument("--out", default="sheet.png")
    p = sub.add_parser("strip"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("y0", type=float); p.add_argument("y1", type=float)
    p.add_argument("--x0", type=float, default=0.04); p.add_argument("--x1", type=float, default=0.96)
    p.add_argument("--dpi", type=int, default=170); p.add_argument("--out", default="strip.png")
    p = sub.add_parser("crop"); p.add_argument("pdf"); p.add_argument("n", type=int); p.add_argument("box")
    p.add_argument("--dpi", type=int, default=300); p.add_argument("--tight", action="store_true")
    p.add_argument("--split", action="store_true"); p.add_argument("--out", default="crop.png")
    p = sub.add_parser("split"); p.add_argument("pdf"); p.add_argument("n", type=int); p.add_argument("box")
    p = sub.add_parser("prep"); p.add_argument("src"); p.add_argument("out")
    p.add_argument("--dpi", type=int, default=150); p.add_argument("--step", type=int, default=50)
    p.add_argument("--page", type=int, default=1)
    p = sub.add_parser("ruler"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--dpi", type=int, default=200); p.add_argument("--step", type=int, default=50)
    p.add_argument("--out", default="ruler.png")
    p = sub.add_parser("zoom"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--box", required=True, help="像素框 x0,y0,x1,y1（基准 dpi 渲染下的像素）")
    p.add_argument("--dpi", type=int, default=200); p.add_argument("--scale", type=float, default=2.0)
    p.add_argument("--step", type=int, default=25); p.add_argument("--out", default="zoom.png")
    p = sub.add_parser("scan"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--dpi", type=int, default=150); p.add_argument("--max-blocks", type=int, default=40)
    p.add_argument("--montage", default=None); p.add_argument("--tile-dpi", type=int, default=180)
    p.add_argument("--tile-width", type=int, default=420)
    p = sub.add_parser("coverage"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--prior", required=True); p.add_argument("--dpi", type=int, default=120)
    p.add_argument("--min-gap", type=float, default=0.01); p.add_argument("--max-gaps", type=int, default=10)
    p = sub.add_parser("leftover"); p.add_argument("pdf"); p.add_argument("n", type=int)
    p.add_argument("--prior", required=True, help='已覆盖的框，JSON 数组 [[x0,y0,x1,y1],...] 或文件路径')
    p.add_argument("--dpi", type=int, default=120); p.add_argument("--max-candidates", type=int, default=8)
    p.add_argument("--tile-dpi", type=int, default=200); p.add_argument("--tile-width", type=int, default=520)
    p.add_argument("--min-area", type=float, default=0.03); p.add_argument("--min-ink", type=float, default=0.45)
    p.add_argument("--out", default="leftover.png")
    p = sub.add_parser("export"); p.add_argument("pdf"); p.add_argument("out"); p.add_argument("spec")
    p.add_argument("--dpi", type=int, default=300); p.add_argument("--tight", action="store_true")
    p = sub.add_parser("selftest"); p.add_argument("--out-dir", default=".pdfx-fixtures")

    p = sub.add_parser("view", help="取图 / 刻度 / 画框 / 打包：都是开关，可组合")
    p.add_argument("input", help="PDF 或页面图片")
    p.add_argument("--out", required=True, help="输出图片路径")
    p.add_argument("--page", type=int, default=1, help="默认页号，从 1 开始")
    p.add_argument("--box", default="", help="取一块：整页像素；可写页号前缀 3:x0,y0,x1,y1")
    p.add_argument("--scale", type=float, default=0, help="缩放；0=原样，负数=按 --target 放大")
    p.add_argument("--target", type=int, default=1200, help="自动缩放的目标宽度（像素）")
    p.add_argument("--ruler", action="store_true", help="叠加刻度")
    p.add_argument("--step", type=int, default=0, help="刻度间隔（整页像素）；0=自动")
    p.add_argument("--boxes", default="", help="画框：整页像素，分号分隔，可写页号前缀")
    p.add_argument("--fit", choices=["none", "union"], default="none",
                   help="union：先裁到 --boxes 的并集再画")
    p.add_argument("--pack", default="", help="打包多块：x0,y0,x1,y1；只写页号表示整页")
    p.add_argument("--gap", type=int, default=26, help="打包时块间缝隙（像素）")
    p.add_argument("--max-width", type=int, default=2000, help="打包画布最大宽度")
    p.add_argument("--map", default="", help="打包时写出各块的页号/原点，供 norm --map 使用")
    p = sub.add_parser("norm", help="把本视图读数换算成整页归一化坐标 0..1")
    p.add_argument("input")
    p.add_argument("--boxes", required=True,
                   help="本视图坐标，分号分隔；用 --map 时写成 块号:x0,y0,x1,y1")
    p.add_argument("--origin", default="", help="本视图左上角的整页像素 X,Y")
    p.add_argument("--map", default="", help="view --pack 产出的 map.json")
    p.add_argument("--page", type=int, default=1, help="默认页号；用 --map 时按块自带的页号")

    a = ap.parse_args(argv)
    handlers = {"probe": cmd_probe, "page": cmd_page, "sheet": cmd_sheet, "strip": cmd_strip,
                "crop": cmd_crop, "split": cmd_split, "export": cmd_export, "leftover": cmd_leftover,
                "scan": cmd_scan, "coverage": cmd_coverage,
                "ruler": cmd_ruler_page, "zoom": cmd_zoom, "prep": cmd_prep,
                "view": cmd_view, "norm": cmd_norm,
                "selftest": cmd_selftest}
    return handlers[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
