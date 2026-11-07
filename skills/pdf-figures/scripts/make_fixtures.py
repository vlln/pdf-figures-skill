#!/usr/bin/env python3
"""make_fixtures — 生成自检用的两个合成 PDF（无第三方 PDF 写库）。

vector.pdf : 纯矢量页，已知图表矩形、图注、表格，用来验证「墨迹收缩是否精确」。
scan.pdf   : 整页一张 JPEG（旋转 + 噪声 + JPEG 压缩），用来验证「阈值是否自适应」。

写 PDF 是手搓的：只用到 xref 偏移，够 pypdfium2 读即可。
"""

from __future__ import annotations

import os
import random

from PIL import Image, ImageDraw, ImageFilter

PAGE_W, PAGE_H = 612.0, 792.0

# 矢量页里图表矩形的真值（PDF 坐标 -> 归一化、左上原点）
_CHART_PDF = (72.0, 600.0, 332.0, 720.0)
VECTOR_CHART_NORM = (
    _CHART_PDF[0] / PAGE_W,
    (PAGE_H - _CHART_PDF[3]) / PAGE_H,
    _CHART_PDF[2] / PAGE_W,
    (PAGE_H - _CHART_PDF[1]) / PAGE_H,
)

# 扫描页里色块的真值（相对整页归一化）
SCAN_BLOCK_NORM = (200 / 1224, 300 / 1584, 900 / 1224, 800 / 1584)


def _vector_content() -> bytes:
    ops = []
    # 图表底板（灰色填充，是「非黑非白」区域，墨迹阈值必须能抓到）
    ops.append("q 0.85 g 72 600 260 120 re f Q")
    bars = [(96, 45), (128, 78), (160, 60), (192, 95), (224, 52)]
    for x, h in bars:
        ops.append(f"q 0 g {x} 600 20 {h} re f Q")
    ops.append("q 0 0 0 RG 1 w 72 600 m 332 600 l S Q")
    ops.append("BT /F1 9 Tf 72 570 Td (Figure 1: Fold change across five genes.) Tj ET")
    # 表格：灰色表头 + 分隔线
    ops.append("q 0.8 g 72 500 468 18 re f Q")
    for i in range(5):
        y = 500 - i * 22
        ops.append(f"q 0.4 G 0.6 w 72 {y} m 540 {y} l S Q")
    return ("\n".join(ops) + "\n").encode("latin-1")


def _scan_page() -> bytes:
    im = Image.new("RGB", (1224, 1584), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([200, 300, 900, 800], fill=(210, 210, 210))
    for i, h in enumerate([120, 200, 150, 260, 140]):
        x = 260 + i * 110
        d.rectangle([x, 790 - h, x + 60, 790], fill=(20, 20, 20))
    d.line([200, 790, 900, 790], fill=(0, 0, 0), width=3)
    im = im.rotate(0.6, resample=Image.BICUBIC, fillcolor=(255, 255, 255))

    rnd = random.Random(7)
    px = im.load()
    for y in range(0, 1584, 2):
        for x in range(0, 1224, 2):
            v = px[x, y][0] + rnd.randint(-12, 12)
            v = max(0, min(255, v))
            px[x, y] = (v, v, v)
    im = im.filter(ImageFilter.GaussianBlur(0.4))

    buf = __import__("io").BytesIO()
    im.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def _write_pdf(path: str, objects: list[bytes]) -> None:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    with open(path, "wb") as fh:
        fh.write(out)


def _stream(body: bytes) -> bytes:
    return b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"


def build(dirpath: str) -> dict:
    os.makedirs(dirpath, exist_ok=True)

    # --- vector.pdf：1 页，纯矢量 ---
    c1 = _vector_content()
    vector_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        _stream(c1),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    # --- scan.pdf：1 页，整页一张 JPEG ---
    jpeg = _scan_page()
    with Image.open(__import__("io").BytesIO(jpeg)) as im:
        iw, ih = im.size
    c2 = f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im1 Do Q".encode()
    scan_objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /XObject << /Im1 5 0 R >> >> /Contents 4 0 R >>",
        _stream(c2),
        b"<< /Type /XObject /Subtype /Image /Width " + str(iw).encode()
        + b" /Height " + str(ih).encode()
        + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
        + str(len(jpeg)).encode() + b" >>\nstream\n" + jpeg + b"\nendstream",
    ]

    vpath = os.path.join(dirpath, "vector.pdf")
    spath = os.path.join(dirpath, "scan.pdf")
    _write_pdf(vpath, vector_objects)
    _write_pdf(spath, scan_objects)
    return {"vector": vpath, "scan": spath}


if __name__ == "__main__":
    import sys

    print(build(sys.argv[1] if len(sys.argv) > 1 else ".pdfx-fixtures"))
