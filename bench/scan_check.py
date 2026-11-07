#!/usr/bin/env python3
"""scan_check — 在扫描退化集上验证脚本层（不经过模型，确定性）。

检验的是 skill 的核心承诺：同一套像素命令在纯位图页面上仍然能精确收缩到内容边界。
做法：取 GT 框外扩 5% 作为「模型给的粗框」，跑 pdfx 的墨迹收缩，再与 GT 比 IoU。

这一项不依赖模型、不走网络，是可反复运行的回归检查。

用法：python3 bench/scan_check.py --set ~/References/pdf-figures-bench/scan30 --images .../scan30/images
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "pdf-figures", "scripts")


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter)


CODE = """
import json,sys
sys.path.insert(0, %r)
from pdfx import Doc, background, page_threshold, trim, normalize
d = Doc(sys.argv[1])
im = d.render(0, 110)
th = page_threshold(d.render(0, 72))
bb, touch = trim(im, th)
W, H = im.size
print(json.dumps({"box": [bb[0]/W, bb[1]/H, bb[2]/W, bb[3]/H] if bb else None,
                  "touch": touch, "th": th, "bg": background(normalize(d.render(0, 72)))}))
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--images", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pad", type=float, default=0.03)
    ap.add_argument("--clean-images", default="", help="原始未退化页面目录，用于隔离退化影响")
    ap.add_argument("--clean-manifest", default="", help="原始集的 manifest.json（id -> image 映射）")
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    with open(os.path.join(a.set_dir, "gt_graphic_only.dev.json"), "r", encoding="utf-8") as fh:
        gt = {p["id"]: p["boxes"] for p in json.load(fh)["pages"]}

    pages = manifest["pages"][: a.limit or None]
    id2img = {}
    if a.clean_manifest:
        with open(a.clean_manifest, "r", encoding="utf-8") as fh:
            id2img = {p["id"]: p["image"] for p in json.load(fh)["pages"]}
    code = CODE % SCRIPTS
    rows, failed = [], []
    for page in pages:
        boxes = gt.get(page["id"]) or []
        if not boxes:
            continue
        g = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        coarse = [max(0.0, g[0] - a.pad), max(0.0, g[1] - a.pad),
                  min(1.0, g[2] + a.pad), min(1.0, g[3] + a.pad)]
        src = os.path.join(a.images, page["image"])
        clean_src = (os.path.join(a.clean_images, id2img[page["id"]])
                     if a.clean_images and page["id"] in id2img else None)
        # 先把外扩框裁出来，再单独收缩，模拟「模型给粗框 -> 脚本收紧」
        crop_code = code.replace(
            "im = d.render(0, 110)", f"im = d.render(0, 150, tuple({coarse!r}))")
        r = subprocess.run([sys.executable, "-c", crop_code, src], capture_output=True, text=True)
        if r.returncode != 0:
            failed.append({"id": page["id"], "err": r.stderr.strip()[-160:]})
            continue
        rec = json.loads(r.stdout)
        if not rec["box"]:
            failed.append({"id": page["id"], "err": "empty trim"})
            continue
        # 把框从「裁剪局部」换算回整页
        b = rec["box"]
        sx = coarse[2] - coarse[0]
        sy = coarse[3] - coarse[1]
        page_box = [coarse[0] + b[0] * sx, coarse[1] + b[1] * sy,
                    coarse[0] + b[2] * sx, coarse[1] + b[3] * sy]
        iou_clean = None
        if clean_src and os.path.exists(clean_src):
            rc = subprocess.run([sys.executable, "-c", crop_code, clean_src], capture_output=True, text=True)
            if rc.returncode == 0:
                c = json.loads(rc.stdout)["box"]
                if c:
                    pcb = [coarse[0] + c[0] * sx, coarse[1] + c[1] * sy,
                           coarse[0] + c[2] * sx, coarse[1] + c[3] * sy]
                    iou_clean = round(iou(page_box, pcb), 4)
        rows.append({"id": page["id"], "iou": round(iou(page_box, g), 4), "iou_vs_clean": iou_clean,
                     "touch": rec["touch"], "th": rec["th"], "bg": rec["bg"],
                     "area_ratio": round((b[2] - b[0]) * (b[3] - b[1]) / (sx * sy), 3)})

    ious = [r["iou"] for r in rows]
    collapse = [r for r in rows if r["area_ratio"] > 0.9]
    touched = [r for r in rows if any(r["touch"].values())]
    n = max(1, len(rows))
    out = {
        "pages": len(rows), "failed": failed,
        "mean_iou": round(sum(ious) / n, 4),
        "pass_iou_0.5": round(sum(1 for v in ious if v >= 0.5) / n, 3),
        "pass_iou_0.75": round(sum(1 for v in ious if v >= 0.75) / n, 3),
        "collapsed_to_full_page": len(collapse),
        "touched_edge": len(touched),
        "mean_iou_vs_clean": round(sum(r["iou_vs_clean"] for r in rows if r["iou_vs_clean"] is not None)
                                  / max(1, sum(1 for r in rows if r["iou_vs_clean"] is not None)), 4),
        "threshold_range": [min((r["th"] for r in rows), default=None),
                            max((r["th"] for r in rows), default=None)],
        "background_range": [min((r["bg"] for r in rows), default=None),
                             max((r["bg"] for r in rows), default=None)],
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
