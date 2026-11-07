#!/usr/bin/env bash
# pipeline.sh — 一条命令跑完一个条件：取输入 → 跑 Agent → 评分 → 汇总。
#
# 用法：
#   bench/pipeline.sh <set_dir> <split> <images_dir> <prompt> <name> <out_dir> [limit]
#
# 例：
#   bench/pipeline.sh ~/References/pdf-figures-bench/acad2 dev \
#     ~/References/pdf-figures-bench/omnidocbench/images bench/prompts/baseline.txt B-dev \
#     .work/runs/B-dev 10
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SKILL="$ROOT/skills/pdf-figures"

SET="${1:?set_dir}"; SPLIT="${2:?split}"; IMAGES="${3:?images}"; PROMPT="${4:?prompt}"
NAME="${5:?name}"; OUT="${6:?out_dir}"; LIMIT="${7:-0}"

mkdir -p "$OUT"
python3 "$HERE/agents.py" --set "$SET" --split "$SPLIT" --pages "$IMAGES" \
  --prompt "$PROMPT" --name "$NAME" --out "$OUT/pred.jsonl" \
  --limit "$LIMIT" --timeout 300 --skill "$SKILL"

for v in graphic_only with_caption; do
  [ -f "$SET/gt_$v.$SPLIT.json" ] || continue
  python3 "$HERE/score.py" --gt "$SET/gt_$v.$SPLIT.json" --pred "$OUT/pred.jsonl" \
    --only-pred --json "$OUT/scoring/$v.json" || true
done
python3 - "$OUT" <<'PY'
import json, sys, os
out = sys.argv[1]
row = {"run": os.path.basename(out)}
for v in ("graphic_only", "with_caption"):
    p = os.path.join(out, "scoring", f"{v}.json")
    if os.path.exists(p):
        d = json.load(open(p))
        row[v] = {k: d[k] for k in ("pages", "precision", "recall", "f1", "merged_f1", "miou")}
print(json.dumps(row, ensure_ascii=False, indent=2))
PY
