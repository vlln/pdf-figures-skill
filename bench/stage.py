#!/usr/bin/env python3
"""stage — 把评测页复制成一个「不透题」的输入目录。

为什么必须做：数据集文件名里带着 id，而 GT 就放在同一块磁盘上。一个会 ls 的 Agent
完全可能读到 acad2/gt_*.json，从而直接抄答案。批次跑出 F1=1.000 时我们必须能排除这条路径。

做法：
  - 复制（不是软链）到 /tmp 下的一次性目录；
  - 文件名改成不透明序号 p001.png…；
  - 映射表写到 staging 目录**之外**的私有文件里，只有评分侧用。

用法：
  python3 bench/stage.py --set <set> --split dev --images <img_dir> --limit 10 \
      --out /tmp/pdfx-eval-xxx
"""

from __future__ import annotations

import argparse
import json
import os
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--images", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = [p for p in manifest["pages"] if p["split"] == a.split]
    if a.limit:
        pages = pages[: a.limit]

    os.makedirs(a.out, exist_ok=True)
    mapping = {}
    for i, p in enumerate(pages, 1):
        src = os.path.join(a.images, p["image"])
        if not os.path.exists(src):
            continue
        ext = os.path.splitext(p["image"])[1].lower() or ".png"
        name = f"p{i:03d}{ext}"
        shutil.copyfile(src, os.path.join(a.out, name))
        mapping[name] = p["id"]

    # 映射表放在上一级，避免被输入目录里的文件列表直接暴露
    with open(a.out.rstrip("/") + ".mapping.json", "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"staged": len(mapping), "out": a.out,
                      "mapping": a.out.rstrip("/") + ".mapping.json"}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
