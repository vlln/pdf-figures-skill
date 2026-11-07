#!/usr/bin/env python3
"""twopass — 强制两步：先出框，再把「框外剩余区域」拼成选择题让模型挑。

这是 leftover 的交互式版本，也是技能内部期望的流程：
  phase 1  模型正常给框
  phase 2  脚本算出未覆盖区域 → 编号拼图 → 模型只回答「保留哪几个编号」
  merge    把选中的候选框并进去，按 IoU 去重

phase 2 是纯选择题，不要求模型再去看整页，因此比「让模型自己复查」稳定得多。

用法：
  python3 bench/twopass.py --set <set> --split test --images <img_dir> \
      --pred .work/runs/testB/pred.jsonl --out .work/runs/testB2/pred.jsonl --limit 20
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

from pdfx import Doc  # noqa: E402
import pdfx  # noqa: E402

PROMPT = """工作目录里有若干图片，每张是「某页 PDF 中尚未被框住的区域」的候选拼图。
文件名对应关系：
{pairs}

请逐张判断：图里那些带编号的局部（#1、#2 …），哪些是**图（figure）**或**表（table）**？
判断标准：图表应当是数据图、示意图、照片、带行列结构的表格；正文段落、标题、公式、
页眉页脚都不算。

只输出一行 JSON，键是图片文件名，值是要保留的编号数组：

{{"pages": {{"{example}": [1, 3]}}}}

整张都没有图表就给空数组。不要解释、不要代码块。
"""


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="test")
    ap.add_argument("--images", required=True)
    ap.add_argument("--pred", required=True, help="phase 1 的 pred.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=None, help="拼图输出目录")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        man = json.load(fh)
    pages = [p for p in man["pages"] if p["split"] == a.split][: a.limit]
    phase1 = {json.loads(l)["id"]: json.loads(l)["boxes"] for l in open(a.pred, "r", encoding="utf-8")}

    work = a.work or (a.out + ".leftover")
    os.makedirs(work, exist_ok=True)

    pairs, cand_map = [], {}
    t0 = time.time()
    for k, p in enumerate(pages, 1):
        pid = p["id"]
        img = os.path.join(a.images, p["image"])
        doc = Doc(img)
        cands = pdfx.cmd_leftover_direct(doc, phase1.get(pid, []), 120, 8, 200, 400)
        if not cands:
            continue
        name = f"left_{k:03d}.png"
        # 复用 cmd_leftover 的绘制逻辑：直接调用命令层太重，这里就地重画
        _render_montage(doc, cands, os.path.join(work, name))
        pairs.append(f"{name}  →  {pid}")
        cand_map[name] = (pid, cands)

    if not pairs:
        print(json.dumps({"pages": len(pages), "with_candidates": 0}, ensure_ascii=False))
        _write(a.out, pages, phase1, {})
        return 0

    example = next(iter(cand_map))
    task = PROMPT.replace("{pairs}", "\n".join(pairs)).replace("{example}", example)
    proc = subprocess.Popen(["dsh", "headless", task], cwd=work, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        raw, err = proc.communicate(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        raw, err = proc.communicate()

    with open(a.out + ".raw.txt", "w", encoding="utf-8") as fh:
        fh.write(raw or "")

    picks = {}
    for obj in _dicts(raw or ""):
        if isinstance(obj.get("pages"), dict):
            picks = obj["pages"]

    merged, kept = {}, 0
    for name, (pid, cands) in cand_map.items():
        sel = picks.get(name) or []
        boxes = list(phase1.get(pid, []))
        for i in sel:
            try:
                c = cands[int(i) - 1]
            except (ValueError, IndexError):
                continue
            if not any(iou(c, b) > 0.6 for b in boxes):
                boxes.append(c)
                kept += 1
        merged[pid] = boxes

    _write(a.out, pages, phase1, merged)
    print(json.dumps({"pages": len(pages), "with_candidates": len(cand_map),
                      "selected_boxes_added": kept, "elapsed_sec": round(time.time() - t0, 1),
                      "out": a.out}, ensure_ascii=False))
    return 0


def _render_montage(doc, cands, path):
    from PIL import Image, ImageDraw
    tiles = []
    for k, b in enumerate(cands, 1):
        sub = doc.render(0, 200, tuple(b))
        tw = 520
        th = max(1, int(sub.size[1] * tw / sub.size[0]))
        sub = sub.resize((tw, th), Image.LANCZOS)
        tile = Image.new("RGB", (tw + 8, th + 26), (245, 245, 245))
        tile.paste(sub, (4, 22))
        ImageDraw.Draw(tile).text((6, 4), f"#{k}", fill=(180, 0, 0))
        tiles.append(tile)
    canvas = Image.new("RGB", (tiles[0].size[0], sum(t.size[1] for t in tiles)), (255, 255, 255))
    y = 0
    for t in tiles:
        canvas.paste(t, (0, y))
        y += t.size[1]
    canvas.save(path)


def _dicts(text):
    dec = json.JSONDecoder()
    out, i = [], text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                out.append(obj)
            i = text.find("{", max(end, i + 1))
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    return out


def _write(out, pages, phase1, merged):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for p in pages:
            pid = p["id"]
            fh.write(json.dumps({"id": pid, "boxes": merged.get(pid, phase1.get(pid, [])),
                                 "meta": {"name": "twopass"}}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
