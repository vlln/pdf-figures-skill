#!/usr/bin/env python3
"""inputs — 把评测集的页面图包成单页 PDF（只依赖标准库 + Pillow）。

为什么必须做这一步：OmniDocBench 只发页面图，MinerU 只吃 PDF。三方必须吃同一份输入，
否则比的是渲染器而不是方法。包成 PDF 之后，pdfx 的渲染路径与真实 PDF 完全一致。

用法：
  python3 bench/inputs.py --manifest acad/manifest.json --images <page_dir> --out pdfs/
"""

from __future__ import annotations

import argparse
import json
import os

from PIL import Image


def _write_pdf(path: str, objects: list[bytes]) -> None:
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
    with open(path, "wb") as fh:
        fh.write(out)


def image_to_pdf(img_path: str, pdf_path: str) -> tuple[int, int]:
    with Image.open(img_path) as im:
        w, h = im.size
        rgb = im.convert("RGB")
        buf = __import__("io").BytesIO()
        rgb.save(buf, format="JPEG", quality=92)
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
    _write_pdf(pdf_path, objects)
    return w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", default=None)
    a = ap.parse_args()

    with open(a.manifest, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    os.makedirs(a.out, exist_ok=True)
    done, missing = [], []
    for page in manifest["pages"]:
        if a.split and page["split"] != a.split:
            continue
        src = os.path.join(a.images, page["image"])
        dst = os.path.join(a.out, page["id"] + ".pdf")
        if not os.path.exists(src):
            missing.append(page["id"])
            continue
        image_to_pdf(src, dst)
        done.append({"id": page["id"], "pdf": dst, "src": src})
    print(json.dumps({"written": len(done), "missing": missing[:10], "missing_count": len(missing),
                      "out": a.out}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
