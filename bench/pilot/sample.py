#!/usr/bin/env python3
"""sample — 从冻结的 test 划分里分层随机抽一小批有代表性的页面（只依赖标准库）。

为什么分层而不是简单随机：test 只有 80 页，学术论文占一半以上；8 页简单随机
大概率抽不到杂志、书籍或多目标页，结论会只代表一种版式。

做法：
  1. 每页按「来源 × 目标构成」归入一个层；层定义见 STRATA，按页面属性与标注决定，
     不看任何方法的历史得分。
  2. 每层用固定种子随机取 1 页；若整体中文页少于 MIN_ZH，换下一个种子偏移重抽，
     并把实际用的偏移写进清单。
  3. 输出 manifest（含 sha256 与分层理由）+ 两种 GT，写到评测数据目录，不进仓库。

用法：
  python3 bench/pilot/sample.py --set ~/References/pdf-figures-bench/acad2 \
      --omni ~/References/pdf-figures-bench/omnidocbench --out ~/References/pdf-figures-bench/pilot8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random

# 层：(名称, 来源组, 目标构成)。构成：fig=只有图，tab=只有表，mix=图表都有，multi=目标≥3。
STRATA = [
    ("acad-fig", "academic", "fig"),
    ("acad-tab", "academic", "tab"),
    ("acad-mix", "academic", "mix"),
    ("acad-multi", "academic", "multi"),
    ("book-tab", "book", "tab"),
    ("book-fig", "book", "fig_or_mix"),
    ("mag-fig", "magazine", "fig"),
    ("mag-multi", "magazine", "multi"),
]
MIN_ZH = 2


def source_group(src: str) -> str:
    if src == "academic_literature":
        return "academic"
    if src in ("book", "colorful_textbook"):
        return "book"
    if src == "magazine":
        return "magazine"
    return "other"


def composition(n_fig: int, n_tab: int) -> set[str]:
    tags = set()
    if n_fig + n_tab >= 3:
        tags.add("multi")
    if n_fig and not n_tab:
        tags.add("fig")
    if n_tab and not n_fig:
        tags.add("tab")
    if n_fig and n_tab:
        tags.add("mix")
    if n_fig:
        tags.add("fig_or_mix")
    return tags


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir", help="冻结评测集目录（manifest + gt_*）")
    ap.add_argument("--omni", required=True, help="OmniDocBench 目录（OmniDocBench.json + images/）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--seed", type=int, default=20261105)
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = {p["image"]: p for p in manifest["pages"] if p["split"] == a.split}
    gts = {}
    for key in ("graphic_only", "with_caption"):
        with open(os.path.join(a.set_dir, f"gt_{key}.{a.split}.json"), "r", encoding="utf-8") as fh:
            gts[key] = {p["id"]: p["boxes"] for p in json.load(fh)["pages"]}

    with open(os.path.join(a.omni, "OmniDocBench.json"), "r", encoding="utf-8") as fh:
        omni = json.load(fh)
    info = {}
    for rec in omni:
        img = os.path.basename(rec["page_info"]["image_path"])
        if img not in pages:
            continue
        attr = rec["page_info"].get("page_attribute") or {}
        dets = [d for d in rec.get("layout_dets") or [] if not d.get("ignore")]
        n_fig = sum(1 for d in dets if d.get("category_type") == "figure")
        n_tab = sum(1 for d in dets if d.get("category_type") == "table")
        info[img] = {"source": attr.get("data_source"), "layout": attr.get("layout"),
                     "language": attr.get("language"), "n_figure": n_fig, "n_table": n_tab,
                     "group": source_group(attr.get("data_source") or ""),
                     "tags": composition(n_fig, n_tab)}

    pools = {}
    for name, group, comp in STRATA:
        pools[name] = sorted(img for img, v in info.items() if v["group"] == group and comp in v["tags"])
        if not pools[name]:
            raise SystemExit(f"stratum {name} is empty")

    picked, offset = None, 0
    for offset in range(1000):
        rng = random.Random(a.seed + offset)
        chosen, used = [], set()
        for name, _g, _c in STRATA:
            # 层之间可能重叠（multi 与 fig 可同时成立）；同一页不重复入选
            options = [img for img in pools[name] if img not in used]
            img = rng.choice(options)
            used.add(img)
            chosen.append((name, img))
        if sum(1 for _n, img in chosen if info[img]["language"] == "simplified_chinese") >= MIN_ZH:
            picked = chosen
            break
    if picked is None:
        raise SystemExit("could not satisfy language constraint")

    os.makedirs(a.out, exist_ok=True)
    rows = []
    for name, img in picked:
        p = pages[img]
        src = os.path.join(a.omni, "images", img)
        digest = sha256(src)
        if digest != p["sha256"]:
            raise SystemExit(f"sha256 mismatch for {img}")
        v = info[img]
        rows.append({"id": p["id"], "image": img, "sha256": digest, "stratum": name,
                     "source": v["source"], "layout": v["layout"], "language": v["language"],
                     "n_figure": v["n_figure"], "n_table": v["n_table"],
                     "n_gt": len(gts["graphic_only"][p["id"]])})

    out_manifest = {
        "name": "pilot", "from_set": os.path.abspath(a.set_dir), "split": a.split,
        "seed": a.seed, "seed_offset": offset, "strata": [s[0] for s in STRATA],
        "pool_sizes": {k: len(v) for k, v in pools.items()}, "min_zh": MIN_ZH,
        "images_dir": os.path.abspath(os.path.join(a.omni, "images")),
        "pages": rows,
    }
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(out_manifest, fh, ensure_ascii=False, indent=2)
    for key in ("graphic_only", "with_caption"):
        obj = {"set": "pilot", "definition": key, "split": a.split,
               "pages": [{"id": r["id"], "boxes": gts[key][r["id"]]} for r in rows]}
        with open(os.path.join(a.out, f"gt_{key}.json"), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)

    print(json.dumps({"out": a.out, "pages": len(rows), "seed_offset": offset,
                      "pool_sizes": out_manifest["pool_sizes"],
                      "picked": [(r["stratum"], r["language"], r["n_figure"], r["n_table"]) for r in rows]},
                     ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
