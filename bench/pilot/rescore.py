#!/usr/bin/env python3
"""rescore — 用当前解析器重新给已有 run 打分，不重新调用模型。

用途：解析规则或评分口径改了之后（例如放宽页面键、修 merged 计分），
用同一批会话事件重算，不必重跑。答案来自每个会话的 events.jsonl 里最后一条 final。

  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/pilot/rescore.py --runs e1-minimal e2-ruler e3-pixels
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, HERE)

import run as R  # noqa: E402  复用同一份解析与坐标换算


def page_boxes(session_dir: str, labels: list[str], cells, mosaic: int) -> dict:
    """从一个会话复现 {page_label: boxes}；与 run.py 里的换算顺序一致。"""
    events = []
    with open(os.path.join(session_dir, "events.jsonl"), "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    text = R.event_stats(events)["final"]
    answer, problems = R.parse_answer(text, labels)
    out = {}
    for lab, cell in zip(labels, cells):
        boxes = (answer or {}).get(lab, [])
        keep = []
        for b in boxes:
            if mosaic > 1 and not R.inside(b, cell):
                continue  # 落在这个 cell 之外的框不计入这一页，与 run.py 一致
            keep.append(R.cell_to_page(b, cell) if mosaic > 1 else [round(v, 4) for v in b])
        out[lab] = keep
    return out, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--pilot", default=os.path.expanduser("~/References/pdf-figures-bench/pilot8"))
    a = ap.parse_args()

    gt_path = os.path.join(a.pilot, "gt_graphic_only.json")
    with open(gt_path, "r", encoding="utf-8") as fh:
        gt = {p["id"]: p["boxes"] for p in json.load(fh)["pages"]}

    rows = []
    for name in a.runs:
        run_dir = os.path.join(ROOT, ".work", "pilot", name)
        cfg = json.load(open(os.path.join(run_dir, "config.json"), encoding="utf-8"))
        sessions = [json.load(open(p, encoding="utf-8"))
                    for p in sorted(glob.glob(os.path.join(run_dir, "sessions", "*", "session.json")))]
        preds, problems = {}, []
        for rec in sessions:
            got, probs = page_boxes(os.path.join(run_dir, "sessions", f"s{rec['session']:02d}"),
                                    rec["labels"], rec["cells"], cfg["mosaic"])
            problems.extend(probs)
            for lab, pid in zip(rec["labels"], rec["pages"]):
                preds[pid] = got[lab]
        pred_path = os.path.join(run_dir, "pred.jsonl")
        with open(pred_path, "w", encoding="utf-8") as fh:
            for pid, boxes in preds.items():
                fh.write(json.dumps({"id": pid, "boxes": boxes}) + "\n")
        out = subprocess.run([sys.executable, os.path.join(BENCH, "score.py"), "--gt", gt_path,
                              "--pred", pred_path, "--json", os.path.join(run_dir, "score.json")],
                             capture_output=True, text=True, check=True)
        score = json.loads(out.stdout)
        summary = json.load(open(os.path.join(run_dir, "summary.json"), encoding="utf-8"))
        summary.update({"f1": score["f1"], "precision": score["precision"], "recall": score["recall"],
                        "merged_f1": score["merged_f1"], "miou": score["miou"],
                        "tp": score["tp"], "fp": score["fp"], "fn": score["fn"],
                        "problems": sorted(set(problems)), "rescored": True})
        with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        rows.append((name, score, summary))

    print(f"{'run':12s} {'F1':>6s} {'mrgF1':>6s} {'P':>6s} {'R':>6s} {'s/页':>6s} {'步/页':>6s} "
          f"{'工具/页':>6s} {'tok/页':>8s}  页面键修正")
    for name, score, summary in rows:
        fixed = [p for p in summary["problems"] if p.startswith("numeric_label")]
        tok = (summary.get("tokens_per_page") or {}).get("totalTokens") or 0
        print(f"{name:12s} {score['f1']:6.3f} {score['merged_f1']:6.3f} {score['precision']:6.3f} "
              f"{score['recall']:6.3f} {summary['sec_per_page']:6.1f} {summary['steps_per_page']:6.2f} "
              f"{summary['tool_calls_per_page']:6.2f} {tok:8d}  {','.join(fixed) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
