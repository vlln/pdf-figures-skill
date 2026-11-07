#!/usr/bin/env python3
"""m1 — 「脚本预定位 + 模型选号」方法的 runner。

流程：
  1. 对每一页跑 pdfx.scan，得到候选块（编号 i -> box）；
  2. 一次 dsh 会话，把各页的编号拼图交给模型，让它只回编号；
  3. 脚本把编号映射回坐标，写成 pred.jsonl。

模型全程不输出任何数字坐标——这是 M1 与其它方法的本质区别。

用法：
  python3 bench/m1.py --set <set> --split test --pages <img_dir> --limit 20 \
      --out .work/runs/M1s/pred.jsonl --mapping <staged mapping.json>
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

import pdfx  # noqa: E402
from pdfx import Doc  # noqa: E402

PROMPT = """工作目录里有若干张页面图，以及按页生成的候选拼图（文件名形如 mont_<页面名>.png）。
每张拼图里的块用 #1、#2 … 编号。

任务：对每一页，指出**哪几个编号是图（figure）或表（table）的本体**。
严格定义：不要包括图注、表题、caption、标题、正文、公式、页眉页脚、页码。
判断依据：图是数据图形/示意图/照片，表有行列结构；正文段落通常是密集的文字块。

只输出一行 JSON，键是**页面文件名**，值是编号数组：

{{"pages": {{"<页面文件名>": [1, 3]}}}}

没有图表的页面给空数组 []。不要解释、不要代码块。

页面与拼图对照：
{pairs}
"""


def parse_pages(text):
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
    for obj in reversed(out):
        if isinstance(obj.get("pages"), dict):
            return obj["pages"]
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="test")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()

    mapping = json.load(open(a.mapping, encoding="utf-8"))
    files = sorted(mapping)[: a.limit]

    work = os.path.abspath(a.out) + ".work"
    os.makedirs(work, exist_ok=True)

    blocks_by_file, pairs = {}, []
    t0 = time.time()
    for f in files:
        src = os.path.join(a.pages, f)
        doc = Doc(src)
        b, _ = pdfx.scan_blocks(doc, 0, 150, 40)
        blocks_by_file[f] = b
        mont = os.path.join(work, "mont_" + f + ".png")
        pdfx.render_montage(doc, b, mont, 180, 420, 0)
        pairs.append(f"{f}  →  mont_{f}.png")

    task = PROMPT.replace("{pairs}", "\n".join(pairs))
    proc = subprocess.Popen(["dsh", "headless", task], cwd=work, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        raw, err = proc.communicate(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        raw, err = proc.communicate()

    open(a.out + ".raw.txt", "w", encoding="utf-8").write(raw or "")
    picks = parse_pages(raw)

    rows = []
    for f in files:
        idx = picks.get(f) or picks.get(os.path.splitext(f)[0]) or []
        boxes = []
        for k in idx if isinstance(idx, list) else []:
            try:
                boxes.append(blocks_by_file[f][int(k) - 1]["box"])
            except (ValueError, IndexError, TypeError):
                continue
        rows.append({"id": mapping[f], "boxes": boxes,
                     "meta": {"name": "M1-select", "picked": len(idx)}})

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"pages": len(rows), "pages_with_picks": sum(1 for r in rows if r["boxes"]),
                      "boxes": sum(len(r["boxes"]) for r in rows),
                      "elapsed_sec": round(time.time() - t0, 1), "out": a.out},
                     ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
