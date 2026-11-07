#!/usr/bin/env python3
"""make_gt — 把 OmniDocBench 的版面标注转成取图评测用的 GT（只依赖标准库）。

OmniDocBench（v1.6）每条标注用 `category_type` 字符串区分，figure / table 与它们的
caption 是分开的块，且 `extra.relation` 里的 `parent_son` 已经给出了配对的权威答案——
优先用关系，缺失时再退回几何最近邻。

评测算两种定义，分开存档、分开评分：
  graphic-only    figure / table 原框（与官方版面口径一致）
  graphic+caption 图/表框 ∪ 配对 caption 框（实际交付物口径）

坐标转成归一化 0..1、左上原点。数据集版本、许可与每页 sha256 写进 manifest。

用法：
  python3 bench/make_gt.py --json OmniDocBench.json --images images/ \
      --out ~/References/pdf-figures-bench/acad --dev 40 --test 80
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random

TARGETS = {"figure", "table"}
CAPTIONS = {"figure_caption", "table_caption"}


def poly_bbox(poly):
    if not poly:
        return None
    xs, ys = poly[0::2], poly[1::2]
    return [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]


def page_size(rec):
    info = rec.get("page_info") or {}
    return float(info.get("width") or 1), float(info.get("height") or 1)


def geometric_match(target, captions, page_h):
    """几何兜底：图注多在图下方、表注在上方；要求水平有重叠且垂直距离很近。"""
    tx0, ty0, tx1, ty1 = target
    best, best_d = None, None
    for c in captions:
        cx0, cy0, cx1, cy1 = c
        if cx1 < tx0 - 0.02 or cx0 > tx1 + 0.02:
            continue
        if cy0 >= ty1 - 0.01:
            d = cy0 - ty1
        elif cy1 <= ty0 + 0.01:
            d = ty0 - cy1
        else:
            d = 0.0
        if d > page_h * 0.12:
            continue
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--images", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dev", type=int, default=40)
    ap.add_argument("--test", type=int, default=80)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--prefix", default="", help="逗号分隔的文件名前缀过滤，如 'page-,book_en'")
    a = ap.parse_args()

    with open(a.json, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("pages") or data.get("data") or []

    candidates, stats = [], {"pages": 0, "no_target": 0, "relation_used": 0, "geo_used": 0}
    for rec in data:
        stats["pages"] += 1
        w, h = page_size(rec)
        dets = rec.get("layout_dets") or []
        by_id, blocks = {}, {}
        for det in dets:
            if det.get("ignore"):
                continue
            cat = det.get("category_type")
            box = poly_bbox(det.get("poly"))
            if cat is None or box is None:
                continue
            aid = det.get("anno_id")
            by_id[aid] = cat
            blocks[aid] = box

        norm = lambda b: [b[0] / w, b[1] / h, b[2] / w, b[3] / h]
        targets = [(aid, norm(b)) for aid, b in blocks.items() if by_id.get(aid) in TARGETS]
        if not targets:
            stats["no_target"] += 1
            continue
        caps = {aid: norm(b) for aid, b in blocks.items() if by_id.get(aid) in CAPTIONS}

        # 权威配对：extra.relation 的 parent_son
        partner = {}
        for rel in (rec.get("extra") or {}).get("relation") or []:
            if rel.get("relation_type") != "parent_son":
                continue
            s, t = rel.get("source_anno_id"), rel.get("target_anno_id")
            if s in blocks and t in caps:
                partner[s] = t
            elif t in blocks and s in caps:
                partner[t] = s

        graphic_only, with_caption = [], []
        for aid, box in targets:
            graphic_only.append(box)
            if aid in partner:
                c = caps[partner[aid]]
                stats["relation_used"] += 1
            else:
                c = geometric_match(box, list(caps.values()), 1.0)
                if c:
                    stats["geo_used"] += 1
            if c:
                with_caption.append([min(box[0], c[0]), min(box[1], c[1]),
                                     max(box[2], c[2]), max(box[3], c[3])])
            else:
                with_caption.append(list(box))

        img = (rec.get("page_info") or {}).get("image_path") or ""
        if not img:
            continue
        if a.prefix and not any(os.path.basename(img).startswith(p) for p in a.prefix.split(",")):
            continue
        candidates.append({"id": os.path.splitext(os.path.basename(img))[0], "image": img,
                           "graphic_only": graphic_only, "with_caption": with_caption})

    random.Random(a.seed).shuffle(candidates)
    picked = candidates[: a.dev + a.test]
    dev, test = picked[: a.dev], picked[a.dev:]
    os.makedirs(a.out, exist_ok=True)

    for name, key in (("gt_graphic_only", "graphic_only"), ("gt_with_caption", "with_caption")):
        for split, rows in (("dev", dev), ("test", test)):
            obj = {"set": "omnidocbench-figures", "definition": key, "split": split,
                   "pages": [{"id": r["id"], "boxes": r[key]} for r in rows]}
            with open(os.path.join(a.out, f"{name}.{split}.json"), "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)

    manifest = {"name": "omnidocbench-figures", "source": "opendatalab/OmniDocBench",
                "license": "CC-BY-4.0（以上游 README 为准）", "seed": a.seed, "pages": []}
    for split, rows in (("dev", dev), ("test", test)):
        for r in rows:
            p = os.path.join(a.images, r["image"])
            sha = None
            if os.path.exists(p):
                hh = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        hh.update(chunk)
                sha = hh.hexdigest()
            manifest["pages"].append({"id": r["id"], "split": split, "image": r["image"], "sha256": sha})
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"candidates": len(candidates), "dev": len(dev), "test": len(test),
                      "figures_dev": sum(len(r["graphic_only"]) for r in dev),
                      "figures_test": sum(len(r["graphic_only"]) for r in test),
                      "captions": stats}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
