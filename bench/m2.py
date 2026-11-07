#!/usr/bin/env python3
"""m2 — 网格下钻法：模型只选格子，坐标由脚本算。

两级结构：
  L1  整页叠加 4×4 编号网格（A1…D4），模型只回答「哪些格子含图/表」——纯选择；
  L2  把选中的格子放大，模型在**这一格内**给相对框（0..1，相对该格）；
  merge 最终坐标 = 格原点 + 相对框 × 格尺寸，再把相邻/重叠的框并起来。

于是模型的相对误差被格尺寸摊薄：整页给的 2% 误差，在 1/4 格内只有 0.5%。

用法：
  python3 bench/m2.py --set <set> --split test --pages <img_dir> --mapping <m.json> \
      --limit 20 --out .work/runs/M2/pred.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "pdf-figures", "scripts")
sys.path.insert(0, SCRIPTS)

from PIL import Image, ImageDraw  # noqa: E402

from pdfx import Doc  # noqa: E402

ROWS = "ABCD"


def grid_image(doc, page=0, cell=None, dpi=130, n=4):
    """渲染整页或某一格，并叠加 n×n 编号网格。返回 (PIL 图, 该图对应的页面区域)。"""
    region = cell or (0.0, 0.0, 1.0, 1.0)
    im = doc.render(page, dpi, region)
    W, H = im.size
    d = ImageDraw.Draw(im)
    for k in range(1, n):
        d.line([(W * k / n, 0), (W * k / n, H)], fill=(255, 90, 90), width=2)
        d.line([(0, H * k / n), (W, H * k / n)], fill=(255, 90, 90), width=2)
    for r in range(n):
        for c in range(n):
            x = W * c / n + 6
            y = H * r / n + 4
            d.rectangle([x - 3, y - 1, x + 26, y + 14], fill=(255, 255, 255))
            d.text((x, y), f"{ROWS[r]}{c + 1}", fill=(200, 0, 0))
    return im, region


def cell_rect(cell_id, region):
    """'B3' -> 归一化页面坐标（相对整页）。"""
    r = ROWS.index(cell_id[0])
    c = int(cell_id[1]) - 1
    x0, y0, x1, y1 = region
    w, h = (x1 - x0) / 4, (y1 - y0) / 4
    return [x0 + c * w, y0 + r * h, x0 + (c + 1) * w, y0 + (r + 1) * h]


def parse_json(text):
    dec = json.JSONDecoder()
    out, i = [], (text or "").find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                out.append(obj)
            i = text.find("{", max(end, i + 1))
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    return out


def run_dsh(task, cwd, timeout):
    p = subprocess.Popen(["dsh", "headless", task], cwd=cwd, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out, err = p.communicate()
    return out or "", err or ""


L1 = """工作目录里有若干张页面图，每张叠加了 4×4 编号网格（左上角标注 A1…D4）。

任务：对每张页面，指出那些**主要被图（figure）或表（table）占据**的格子——
即该格面积的一半以上是图形/表格本身（含图注）。只是擦到边、或只含少量文字的格子不要选。
只回答格子编号，不要给任何坐标。

只输出一行 JSON：

{{"pages": {{"<文件名>": ["B3", "C2"]}}}}

没有图表的页面给空数组。不要解释、不要代码块。

页面清单：
{files}
"""

L2 = """工作目录里有若干张放大图，每张是**某一页的某一格**，文件名形如 `p001.jpg__B3.png`。

任务：在这些放大图里，框出图或表的本体（不含图注、标题、正文），坐标以**这张放大图**为基准，归一化 0..1。

只输出一行 JSON：

{{"boxes": {{"p001.jpg__B3": [[x0,y0,x1,y1]]}}}}

整张都没有图表就给空数组。不要解释、不要代码块。

图片清单：
{files}
"""


def merge_boxes(boxes, gap=0.02):
    """把相互靠近/重叠的框并起来（跨格子的同一幅图）。"""
    changed = True
    while changed:
        changed = False
        out = []
        for b in boxes:
            for k, o in enumerate(out):
                if (b[0] <= o[2] + gap and o[0] <= b[2] + gap
                        and b[1] <= o[3] + gap and o[1] <= b[3] + gap):
                    out[k] = [min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3])]
                    changed = True
                    break
            else:
                out.append(list(b))
        boxes = out
    return boxes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="test")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    mapping = json.load(open(a.mapping, encoding="utf-8"))
    files = sorted(mapping)[: a.limit]
    work = os.path.abspath(a.out) + ".work"
    os.makedirs(os.path.join(work, "l1"), exist_ok=True)
    os.makedirs(os.path.join(work, "l2"), exist_ok=True)

    docs = {f: Doc(os.path.join(a.pages, f)) for f in files}
    t0 = time.time()

    # ---- L1：整页 + 编号网格
    l1_files = []
    for f in files:
        im, _ = grid_image(docs[f])
        name = f + ".png"
        im.save(os.path.join(work, "l1", name))
        l1_files.append(name)
    pages_cells = {}
    raw1_parts = []
    for i in range(0, len(l1_files), 5):          # 一次 20 页会超时，按 5 页一批
        part = l1_files[i:i + 5]
        raw1, _err1 = run_dsh(L1.replace("{files}", "\n".join(part)),
                              os.path.join(work, "l1"), a.timeout)
        raw1_parts.append(raw1)
    raw1 = "\n".join(raw1_parts)
    open(a.out + ".l1.raw.txt", "w", encoding="utf-8").write(raw1)
    for obj in parse_json(raw1):
        if isinstance(obj.get("pages"), dict):
            for k, v in obj["pages"].items():
                # 模型常把扩展名再叠一层（h001.jpg -> h001.jpg.png）
                base = k
                for suf in (".png", ".jpg", ".jpeg"):
                    if base.endswith(suf):
                        base = base[: -len(suf)]
                pages_cells.setdefault(k, v)
                pages_cells.setdefault(base, v)

    # ---- L2：选中的格子放大 + 相对框
    crops, crop_meta = [], {}
    for f in files:
        cells = pages_cells.get(f) or pages_cells.get(os.path.splitext(f)[0]) or []
        for cid in cells if isinstance(cells, list) else []:
            cid = str(cid).strip().upper()
            if len(cid) != 2 or cid[0] not in ROWS or cid[1] not in "1234":
                continue
            rect = cell_rect(cid, (0.0, 0.0, 1.0, 1.0))
            im, _ = grid_image(docs[f], 0, tuple(rect), dpi=260, n=1)
            name = f"{f}__{cid}.png"
            im.save(os.path.join(work, "l2", name))
            crops.append(name)
            crop_meta[name] = (f, rect)

    boxes_by_crop = {}
    CHUNK = 12
    raw_texts = []
    for i in range(0, len(crops), CHUNK):
        part = crops[i:i + CHUNK]
        raw2, _err2 = run_dsh(L2.replace("{files}", "\n".join(part)),
                              os.path.join(work, "l2"), a.timeout)
        raw_texts.append(raw2)
        for obj in parse_json(raw2):
            if isinstance(obj.get("boxes"), dict):
                boxes_by_crop.update(obj["boxes"])
    if raw_texts:
        open(a.out + ".l2.raw.txt", "w", encoding="utf-8").write("\n".join(raw_texts))

    # ---- 合成：格原点 + 相对框 × 格尺寸
    per_page = {f: [] for f in files}
    for name, (f, rect) in crop_meta.items():
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        for b in boxes_by_crop.get(name, []) or []:
            if not (isinstance(b, list) and len(b) == 4):
                continue
            x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in b]
            if x1 <= x0 or y1 <= y0:
                continue
            per_page[f].append([rect[0] + x0 * rw, rect[1] + y0 * rh,
                                rect[0] + x1 * rw, rect[1] + y1 * rh])

    rows = []
    for f in files:
        boxes = merge_boxes(per_page[f])
        boxes = [[round(v, 4) for v in b] for b in boxes]
        rows.append({"id": mapping[f], "boxes": boxes,
                     "meta": {"name": "M2-grid", "cells": len(pages_cells.get(f, []) or [])}})

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"pages": len(rows), "cells_selected": len(crops),
                      "boxes": sum(len(r["boxes"]) for r in rows),
                      "elapsed_sec": round(time.time() - t0, 1), "out": a.out},
                     ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
