#!/usr/bin/env python3
"""run — 批量跑一个评测集上的一个 system，落盘成一次 run。

目录约定（对齐 dsh-evaluation §3）：

  runs/<run-id>/
    run.json        配置、代码版本、输入引用、时间、状态
    manifest.json   有序 id + 每页输入引用
    pred.jsonl      每页预测框（原始输出，评分器唯一的输入）
    scoring/        score.py 的产物与报告

system 说明：
  oracle  直接输出 GT 框——不是方法，是「脚本层上限」对照，用来分离模型定位损失。
  trim    纯脚本下限：整页渲染 → 墨迹包围盒 → 一个框。用来验证链路与量化天花板差距。
  （model 系列在 agents.py 里，走 DSH headless，占位见 --list）

用法：
  python3 bench/run.py --set <dir> --split dev --system oracle --inputs <pdf_dir>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "pdf-figures", "scripts")


def load_set(set_dir: str, split: str):
    with open(os.path.join(set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = [p for p in manifest["pages"] if p["split"] == split]
    with open(os.path.join(set_dir, f"gt_graphic_only.{split}.json"), "r", encoding="utf-8") as fh:
        gt = json.load(fh)
    boxes = {p["id"]: p["boxes"] for p in gt["pages"]}
    return manifest, pages, boxes


def sys_oracle(page, pdf, gt_boxes):
    return gt_boxes[page["id"]], {"system": "oracle"}


def sys_trim(page, pdf, gt_boxes):
    """整页墨迹包围盒：不做语义判断，只验证「全页 → 一个框」这条最短链路。"""
    code = (
        "import json,sys;sys.path.insert(0,%r);"
        "from pdfx import Doc,background,threshold_for,trim;"
        "d=Doc(%r);im=d.render(0,110);"
        "th=threshold_for(background(d.render(0,72)));"
        "bb,t=trim(im,th);W,H=im.size;"
        "print(json.dumps({'box':[bb[0]/W,bb[1]/H,bb[2]/W,bb[3]/H] if bb else None,'touch':t}))"
    ) % (SCRIPTS, pdf)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if out.returncode != 0:
        return [], {"system": "trim", "error": out.stderr.strip()[-300:]}
    rec = json.loads(out.stdout)
    return ([rec["box"]] if rec["box"] else []), {"system": "trim", "touch": rec["touch"]}


SYSTEMS = {"oracle": sys_oracle, "trim": sys_trim}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--system", default="oracle")
    ap.add_argument("--inputs", required=True, help="单页 PDF 目录")
    ap.add_argument("--runs", default=os.path.join(HERE, "runs"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        print(json.dumps({"systems": sorted(SYSTEMS)}, ensure_ascii=False))
        return 0

    manifest, pages, gt_boxes = load_set(a.set_dir, a.split)
    if a.limit:
        pages = pages[: a.limit]

    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{a.system}-{a.split}"
    run_dir = os.path.join(a.runs, run_id)
    os.makedirs(os.path.join(run_dir, "scoring"), exist_ok=True)

    fn = SYSTEMS[a.system]
    preds, t0, errors = [], time.time(), []
    for page in pages:
        pdf = os.path.join(a.inputs, page["id"] + ".pdf")
        if not os.path.exists(pdf):
            errors.append({"id": page["id"], "error": "missing input pdf"})
            continue
        boxes, meta = fn(page, pdf, gt_boxes)
        preds.append({"id": page["id"], "boxes": boxes, "meta": meta})

    elapsed = time.time() - t0
    with open(os.path.join(run_dir, "pred.jsonl"), "w", encoding="utf-8") as fh:
        for rec in preds:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"split": a.split, "pages": [p["id"] for p in pages]}, fh, ensure_ascii=False, indent=2)

    run = {
        "run_id": run_id,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "set": manifest.get("name"),
        "split": a.split,
        "system": a.system,
        "pages": len(pages),
        "completed": len(preds),
        "errors": errors,
        "elapsed_sec": round(elapsed, 2),
        "sec_per_page": round(elapsed / max(1, len(preds)), 3),
        "inputs": os.path.abspath(a.inputs),
        "status": "complete" if not errors else "partial",
    }
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)

    score_js = os.path.join(HERE, "score.py")
    gt_path = os.path.join(a.set_dir, f"gt_graphic_only.{a.split}.json")
    for label, gt in (("graphic_only", gt_path),
                      ("with_caption", os.path.join(a.set_dir, f"gt_with_caption.{a.split}.json"))):
        out_json = os.path.join(run_dir, "scoring", f"{label}.json")
        if not os.path.exists(gt):
            continue
        subprocess.run([sys.executable, score_js, "--gt", gt,
                        "--pred", os.path.join(run_dir, "pred.jsonl"),
                        "--json", out_json], capture_output=True, text=True)
    print(json.dumps({"run_dir": run_dir, "status": run["status"], "pages": len(preds),
                      "sec_per_page": run["sec_per_page"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
