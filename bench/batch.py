#!/usr/bin/env python3
"""batch — 单会话处理整批页面，用于测「批次成本」。

动机：逐页开新会话时，技能的说明、缩略图、工具探索成本被重复计入每一页。
真实使用是一个会话跑整篇。这个脚本把 N 页放进同一个会话，输出与逐页模式同构的
pred.jsonl，可以直接用 score.py 对比。

用法：
  python3 bench/batch.py --set <set> --split dev --pages <img_dir> --limit 10 \
      --out .work/runs/batchC/pred.jsonl --skill <skill_dir>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time

PAGES_RE = re.compile(r'\{"pages"\s*:\s*\{.*?\}\s*\}', re.S)

TEMPLATE = """你要完成一批 PDF 取图任务，共 {n} 页，全部在当前工作目录里：
{files}

{skill_hint}

对每一页，找出所有的「图」（figure）与「表」（table）区域；每个区域包含它自己的
标题/图注（图注通常在下方，表题通常在上方）。多面板图输出一个外框。
不要包含正文段落、公式块、页眉页脚。坐标系：归一化 0..1、左上原点、相对整页。

只输出一行 JSON，键是文件名，值是该页的框数组：

{{"pages": {{"<文件名>": [[x0,y0,x1,y1], ...]}}}}

没有图表的页给空数组。不要任何解释、不要代码块。
"""

SKILL_HINT = """先阅读技能说明 {skill}/SKILL.md，按其中的流程处理；工具是
python3 {skill}/scripts/pdfx.py。可以先用 sheet 之类的低成本方式批量了解这批页面，
再对确实需要的页做精修。但总体时间是预算的一部分，不要为了流程而流程。"""


def extract_dicts(text):
    """从任意文本里扫出所有能解析的 JSON 对象（处理嵌套大括号，避免正则截断）。"""
    dec = json.JSONDecoder()
    out = []
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                out.append(obj)
            i = text.find("{", max(end, i + 1))
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    return out


def parse_pages(text):
    if not text:
        return None
    for obj in reversed(extract_dicts(text)):
        pages = obj.get("pages")
        if isinstance(pages, dict):
            return pages
    for m in reversed(list(PAGES_RE.finditer(text))):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj.get("pages"), dict):
            return obj["pages"]
    return None


def clean(boxes):
    out = []
    for b in boxes or []:
        if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
            x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in b]
            if x1 > x0 and y1 > y0:
                out.append([round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skill", default="")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--staging-mapping", default="", help="stage.py 产出的映射文件，按不透题文件名跑")
    ap.add_argument("--prompt", default="", help="外部模板（含 {n} {files} {skill_hint} 占位符）")
    a = ap.parse_args()

    if a.staging_mapping:
        with open(a.staging_mapping, "r", encoding="utf-8") as fh:
            mapping = json.load(fh)
        files = sorted(mapping.keys())
        by_file = dict(mapping)
        pages = [{"id": mapping[f], "image": f} for f in files]
        if a.limit:
            pages = pages[: a.limit]
            files = [p["image"] for p in pages]
    else:
        with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        pages = [p for p in manifest["pages"] if p["split"] == a.split][: a.limit]
        files = [p["image"] for p in pages]
    by_file = {p["image"]: p["id"] for p in pages}

    skill_abs = os.path.abspath(a.skill) if a.skill else ""
    skill_hint = SKILL_HINT.replace("{skill}", skill_abs) if a.skill else ""
    template = open(a.prompt, "r", encoding="utf-8").read() if a.prompt else TEMPLATE
    task = (template.replace("{n}", str(len(files)))
            .replace("{files}", "\n".join(files))
            .replace("{skill_hint}", skill_hint)
            .replace("{skill}", skill_abs))

    t0 = time.time()
    proc = subprocess.Popen(["dsh", "headless", task], cwd=a.pages, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    timed_out = False
    try:
        raw, err = proc.communicate(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        raw, err = proc.communicate()
    elapsed = time.time() - t0

    with open(a.out + ".raw.txt", "w", encoding="utf-8") as fh:
        fh.write(raw or "")
    got = parse_pages(raw) or {}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for p in pages:
            stem = os.path.splitext(p["image"])[0]
            bystem = {os.path.splitext(k)[0]: v for k, v in got.items()}
            boxes = clean(got.get(p["image"]) or got.get(stem) or bystem.get(stem)
                          or got.get(p["id"]) or [])
            fh.write(json.dumps({"id": p["id"], "boxes": boxes,
                                 "meta": {"name": "batch", "sec": round(elapsed / len(pages), 2),
                                          "content_sha": None}}, ensure_ascii=False) + "\n")

    print(json.dumps({"pages": len(pages), "parsed_pages": len(got), "elapsed_sec": round(elapsed, 1),
                      "sec_per_page": round(elapsed / len(pages), 1), "timed_out": timed_out,
                      "out": a.out, "stderr_tail": (err or "")[-200:]}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
