#!/usr/bin/env python3
"""agents — 把一页 PDF 渲染图交给 DSH headless Agent，收它给出的框。

这是唯一的「薄适配器」：负责读题单、提交、超时、收原始回答；不掺杂评分逻辑。
每页一个独立 Session（dsh headless 天生如此），保证页间不串味。

用法：
  python3 bench/agents.py --set ~/References/pdf-figures-bench/acad --split dev \
      --pages <image_dir> --prompt bench/prompts/baseline.txt --name B-baseline \
      --out runs/<run-id>/pred.jsonl --limit 5
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

BOX_RE = re.compile(r'\{"boxes"\s*:\s*\[.*?\]\s*\}', re.S)


def parse_boxes(text: str):
    """从模型最终回答里抠出最后一个 {"boxes": [...]}。解析失败返回 None。"""
    if not text:
        return None
    for m in reversed(list(BOX_RE.finditer(text))):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        boxes = obj.get("boxes")
        if isinstance(boxes, list):
            out = []
            for b in boxes:
                if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
                    x0, y0, x1, y1 = [float(v) for v in b]
                    x0, x1 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))
                    y0, y1 = max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))
                    if x1 > x0 and y1 > y0:
                        out.append([round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)])
            return out
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--pages", required=True, help="页面图目录")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="已有 pred.jsonl 则只补缺")
    ap.add_argument("--skill", default="", help="技能根目录，用于替换 {skill}")
    a = ap.parse_args()

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = [p for p in manifest["pages"] if p["split"] == a.split]
    if a.limit:
        pages = pages[: a.limit]

    done = {}
    if a.resume and os.path.exists(a.out):
        with open(a.out, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    done[rec["id"]] = rec

    with open(a.prompt, "r", encoding="utf-8") as fh:
        template = fh.read()

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    fh = open(a.out, "a" if a.resume else "w", encoding="utf-8")
    t_all = time.time()
    for i, page in enumerate(pages, 1):
        pid = page["id"]
        if pid in done:
            continue
        img = page["image"]
        if not os.path.exists(os.path.join(a.pages, img)):
            rec = {"id": pid, "boxes": [], "meta": {"name": a.name, "ok": False,
                   "error": "missing image", "sec": 0, "raw_tail": ""}}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            continue
        task = template.replace("{file}", img).replace("{skill}", a.skill)
        t0 = time.time()
        proc = subprocess.Popen(["dsh", "headless", task], cwd=a.pages, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        timed_out = False
        try:
            raw, err_out = proc.communicate(timeout=a.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)   # 连子进程一起收，否则管道不关
            except ProcessLookupError:
                pass
            raw, err_out = proc.communicate()
        boxes = None if timed_out else parse_boxes(raw)
        err = "timeout" if timed_out else (None if boxes is not None else (err_out or "")[-300:])
        rec = {"id": pid, "boxes": boxes or [], "meta": {
            "name": a.name, "sec": round(time.time() - t0, 2),
            "ok": boxes is not None, "error": err, "raw_tail": (raw or "")[-400:]}}
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        print(f"[{i}/{len(pages)}] {pid} boxes={len(boxes or [])} {rec['meta']['sec']}s "
              f"{'' if boxes is not None else 'ERR'}", file=sys.stderr, flush=True)
    fh.close()
    print(json.dumps({"name": a.name, "pages": len(pages), "out": a.out,
                      "elapsed_sec": round(time.time() - t_all, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
