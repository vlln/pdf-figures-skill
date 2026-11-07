#!/usr/bin/env python3
"""make_scan — 由同一批页面生成「扫描件」鲁棒性集（退化消融）。

为什么用合成退化而不是另找扫描集：同一页、同一份 GT，只改变「有没有可解析的矢量/文本」，
把变量隔离成单一因素。退化包含旋转、噪声、模糊与低质量 JPEG——正好是 pdfx 自适应阈值
要对付的那几样。

注意：GT 沿用原页坐标。旋转用 expand=False，画布不变，内容位移 <1%，在 IoU 容差内。

用法：
  python3 bench/make_scan.py --set ~/References/pdf-figures-bench/acad2 \
      --images ~/References/pdf-figures-bench/omnidocbench/images \
      --out ~/References/pdf-figures-bench/scan30 --n 30
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random

from PIL import Image, ImageFilter


def degrade(src: str, dst: str, seed: int) -> None:
    rnd = random.Random(seed)
    with Image.open(src) as im:
        g = im.convert("L")
        g = g.rotate(rnd.uniform(0.3, 0.8), resample=Image.BICUBIC, fillcolor=250, expand=False)
        px = g.load()
        w, h = g.size
        step = 2
        for y in range(0, h, step):
            for x in range(0, w, step):
                v = px[x, y] + rnd.randint(-11, 11)
                v = 0 if v < 0 else (255 if v > 255 else v)
                px[x, y] = v
        g = g.filter(ImageFilter.GaussianBlur(0.5))
        g.convert("RGB").save(dst, format="JPEG", quality=55)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    dev = [p for p in manifest["pages"] if p["split"] == "dev"][: a.n]

    out_img = os.path.join(a.out, "images")
    os.makedirs(out_img, exist_ok=True)
    pages = []
    for i, p in enumerate(dev):
        src = os.path.join(a.images, p["image"])
        if not os.path.exists(src):
            continue
        name = p["id"] + ".jpg"
        dst = os.path.join(out_img, name)
        degrade(src, dst, a.seed + i)
        h = hashlib.sha256()
        with open(dst, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        pages.append({"id": p["id"], "split": "dev", "image": name,
                      "sha256": h.hexdigest(), "bytes": os.path.getsize(dst)})

    new_manifest = {"name": "acad2-scan30", "source": "acad2 (degraded)",
                    "note": "同一批页面的扫描退化消融：旋转+噪声+模糊+JPEG q55",
                    "pages": pages, "page_count": {"dev": len(pages)}}
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(new_manifest, fh, ensure_ascii=False, indent=2)

    for gt_name in ("gt_graphic_only", "gt_with_caption"):
        src = os.path.join(a.set_dir, f"{gt_name}.dev.json")
        with open(src, "r", encoding="utf-8") as fh:
            gt = json.load(fh)
        keep = {p["id"] for p in pages}
        obj = {"set": "acad2-scan30", "definition": gt["definition"], "split": "dev",
               "pages": [p for p in gt["pages"] if p["id"] in keep]}
        with open(os.path.join(a.out, f"{gt_name}.dev.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"pages": len(pages), "out": a.out}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
