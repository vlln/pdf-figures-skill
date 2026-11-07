#!/usr/bin/env python3
"""report — 把各 run 的评分汇总成一张表（只依赖标准库）。

用法：
  python3 bench/report.py --runs runs/ [--out REPORT.md]
"""

from __future__ import annotations

import argparse
import glob
import json
import os

COLS = ["set", "split", "system", "pages", "tp", "fp", "fn",
        "precision", "recall", "f1", "miou", "sec_per_page"]


def load_run(run_dir: str):
    rp = os.path.join(run_dir, "run.json")
    if not os.path.exists(rp):
        return None
    with open(rp, "r", encoding="utf-8") as fh:
        run = json.load(fh)
    row = {"run": os.path.basename(run_dir), "set": run.get("set"), "split": run.get("split"),
           "system": run.get("system"), "pages": run.get("completed"),
           "sec_per_page": run.get("sec_per_page"), "status": run.get("status")}
    for variant in ("graphic_only", "with_caption"):
        sp = os.path.join(run_dir, "scoring", f"{variant}.json")
        if not os.path.exists(sp):
            continue
        with open(sp, "r", encoding="utf-8") as fh:
            s = json.load(fh)
        for k in ("tp", "fp", "fn", "precision", "recall", "f1", "miou"):
            row[f"{k}"] = s.get(k)
        row[f"{variant}_f1"] = s.get("f1")
        row[f"{variant}_miou"] = s.get("miou")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    rows = [r for r in (load_run(d) for d in sorted(glob.glob(os.path.join(a.runs, "*")))) if r]
    lines = ["# 取图评测汇总", "",
             "口径：IoU≥0.5 贪心一对一匹配；预测框面积 >60% 页面积计 FP；"
             "`graphic_only` 与 `with_caption` 分开报。", "",
             "| run | split | system | pages | P | R | F1 | mIoU | F1(+cap) | s/page |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        def g(k):
            v = r.get(k)
            return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
        lines.append("| {run} | {split} | {system} | {pages} | {p} | {rc} | {f1} | {mi} | {f1c} | {sp} |".format(
            run=r["run"], split=r.get("split"), system=r.get("system"), pages=r.get("pages"),
            p=g("precision"), rc=g("recall"), f1=g("f1"), mi=g("miou"),
            f1c=g("with_caption_f1"), sp=g("sec_per_page")))
    if not rows:
        lines.append("| （还没有 run） | | | | | | | | | |")
    text = "\n".join(lines) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
