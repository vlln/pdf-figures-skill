#!/usr/bin/env python3
"""cands_check — 检验「确定性候选块」能不能覆盖 GT（不上模型）。

思路：留出集上两边都漏掉三分之一，是召回问题。如果纯像素方法能枚举出覆盖 90%+ GT 的
候选块，那么「枚举 → 让模型逐块确认」就有希望把召回抬上去；如果覆盖率不够，这条路不用试。

做法（全是确定性像素运算）：
  1. 150 dpi 渲染，自适应阈值取墨迹；
  2. 3×3 腐蚀一次，抹掉文字笔画，留下实心色块/粗线；
  3. 降到 1/4 尺寸做连通域，按面积筛；
  4. 把相邻块合并成外接矩形；
  5. 用 GT 框衡量覆盖率：候选框与 GT 的 IoU≥0.5，或候选框完整包含 GT。

用法：python3 bench/cands_check.py --set ... --images ... --split test --limit 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "skills", "pdf-figures", "scripts"))
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from pdfx import Doc, ink, normalize  # noqa: E402


def label(mask):
    """小数组上的两遍并查集连通域；返回 [(x0,y0,x1,y1,area)]。"""
    H, W = len(mask), len(mask[0])
    parent = list(range(H * W))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for y in range(H):
        base = y * W
        for x in range(W):
            if not mask[y][x]:
                continue
            i = base + x
            if x and mask[y][x - 1]:
                union(i, i - 1)
            if y and mask[y - 1][x]:
                union(i, i - W)
            if y and x and mask[y - 1][x - 1]:
                union(i, i - W - 1)
            if y and x + 1 < W and mask[y - 1][x + 1]:
                union(i, i - W + 1)

    boxes = {}
    for y in range(H):
        base = y * W
        for x in range(W):
            if mask[y][x]:
                r = find(base + x)
                b = boxes.get(r)
                if b is None:
                    boxes[r] = [x, y, x + 1, y + 1, 1]
                else:
                    b[0] = min(b[0], x); b[1] = min(b[1], y)
                    b[2] = max(b[2], x + 1); b[3] = max(b[3], y + 1)
                    b[4] += 1
    return [tuple(b) for b in boxes.values()]


def mask_rows(im):
    W, H = im.size
    px = im.load()
    return [[px[x, y] > 0 for x in range(W)] for y in range(H)]


def candidates(doc, i, dpi=150, scale=4, min_area=0.0015, merge_gap=0.012):
    im = doc.render(i, dpi)
    m = ink(im)                                   # 255 = 墨迹
    m = m.filter(ImageFilter.MinFilter(3))        # 腐蚀，抹掉细笔画
    W, H = m.size
    small = m.resize((max(8, W // scale), max(8, H // scale)), Image.BOX)
    grid = mask_rows(small)
    comps = label(grid)
    sw, sh = small.size
    boxes = []
    for x0, y0, x1, y1, a in comps:
        if (x1 - x0) * (y1 - y0) < min_area * sw * sh:
            continue
        boxes.append([x0 / sw, y0 / sh, x1 / sw, y1 / sh])
    # 简单合并：相互靠近或包含的块合成一个
    changed = True
    while changed:
        changed = False
        out = []
        while boxes:
            b = boxes.pop()
            for k, o in enumerate(boxes):
                if (b[0] <= o[2] + merge_gap and o[0] <= b[2] + merge_gap
                        and b[1] <= o[3] + merge_gap and o[1] <= b[3] + merge_gap):
                    boxes[k] = [min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3])]
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def contains(c, g):
    return c[0] <= g[0] + 0.01 and c[1] <= g[1] + 0.01 and c[2] >= g[2] - 0.01 and c[3] >= g[3] - 0.01


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--images", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        man = json.load(fh)
    with open(os.path.join(a.set_dir, f"gt_graphic_only.{a.split}.json"), "r", encoding="utf-8") as fh:
        gt = {p["id"]: p["boxes"] for p in json.load(fh)["pages"]}

    pages = [p for p in man["pages"] if p["split"] == a.split][: a.limit]
    hit = total = 0
    ncand = 0
    per_page = []
    for p in pages:
        src = os.path.join(a.images, p["image"])
        doc = Doc(src)
        cands = candidates(doc, 0)
        boxes = gt.get(p["id"], [])
        h = sum(1 for g in boxes if any(iou(c, g) >= 0.5 or contains(c, g) for c in cands))
        hit += h
        total += len(boxes)
        ncand += len(cands)
        per_page.append({"id": p["id"], "gt": len(boxes), "hit": h, "cands": len(cands)})

    print(json.dumps({
        "pages": len(pages), "gt_boxes": total, "covered": hit,
        "coverage": round(hit / total, 3) if total else None,
        "cands_total": ncand, "cands_per_page": round(ncand / max(1, len(pages)), 1),
        "pages_full_miss": sum(1 for r in per_page if r["gt"] and r["hit"] == 0),
    }, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
