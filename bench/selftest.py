#!/usr/bin/env python3
"""selftest — 评测链路的最小验证（合成输入，确定性，不联网）。

对应 dsh-evaluation §5：先用合成输入验证「提交 → 收集 → 评分」这条链路，
再去跑真实批次。这里验证评分器的匹配规则、反作弊规则和缺页处理。

用法：python3 bench/selftest.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCORE = os.path.join(HERE, "score.py")

CASES = [
    {
        "name": "perfect",
        "gt": [[0.1, 0.1, 0.5, 0.4]],
        "pred": [[0.1, 0.1, 0.5, 0.4]],
        "want": {"tp": 1, "fp": 0, "fn": 0},
    },
    {
        "name": "miss",
        "gt": [[0.1, 0.1, 0.5, 0.4]],
        "pred": [],
        "want": {"tp": 0, "fp": 0, "fn": 1},
    },
    {
        "name": "half_overlap_fails_at_0.5",
        "gt": [[0.10, 0.10, 0.50, 0.50]],
        "pred": [[0.10, 0.10, 0.30, 0.50]],  # IoU = 0.5 * 0.4 / (0.4) ≈ 0.5 边界
        "want": {"tp": 0, "fp": 1, "fn": 1},
    },
    {
        "name": "inflated_box_is_fp",
        "gt": [[0.1, 0.1, 0.5, 0.4]],
        "pred": [[0.0, 0.0, 1.0, 1.0]],  # 整页框，反作弊必须判 FP
        "want": {"tp": 0, "fp": 1, "fn": 1},
    },
    {
        "name": "large_true_figure_is_tp",   # test 集里真实存在 0.67 页面积的图
        "gt": [[0.05, 0.10, 0.95, 0.85]],
        "pred": [[0.05, 0.10, 0.95, 0.85]],
        "want": {"tp": 1, "fp": 0, "fn": 0},
    },
    {
        "name": "two_figures_one_found",
        "gt": [[0.1, 0.1, 0.4, 0.3], [0.6, 0.1, 0.9, 0.3]],
        "pred": [[0.1, 0.1, 0.4, 0.3]],
        "want": {"tp": 1, "fp": 0, "fn": 1},
    },
    {
        "name": "duplicate_pred_is_fp",
        "gt": [[0.1, 0.1, 0.4, 0.3]],
        "pred": [[0.1, 0.1, 0.4, 0.3], [0.11, 0.11, 0.41, 0.31]],
        "want": {"tp": 1, "fp": 1, "fn": 0},
    },
    {
        "name": "merged_unmatched_pred_is_one_fp",
        "gt": [[0.1, 0.1, 0.3, 0.3]],
        "pred": [[0.7, 0.7, 0.9, 0.9]],
        "want": {"tp": 0, "fp": 1, "fn": 1,
                 "merged_tp": 0, "merged_fp": 1, "merged_fn": 1},
    },
    {
        "name": "merged_two_panels_no_fp",
        "gt": [[0.1, 0.1, 0.3, 0.3], [0.4, 0.1, 0.6, 0.3]],
        "pred": [[0.1, 0.1, 0.6, 0.3]],
        "want": {"tp": 0, "fp": 1, "fn": 2,
                 "merged_tp": 2, "merged_fp": 0, "merged_fn": 0},
    },
    {
        "name": "merged_oversized_box_still_fp",
        "gt": [[0.0, 0.0, 0.4, 1.0], [0.6, 0.0, 1.0, 1.0]],
        "pred": [[0.0, 0.0, 1.0, 1.0]],
        "want": {"tp": 0, "fp": 1, "fn": 2,
                 "merged_tp": 0, "merged_fp": 1, "merged_fn": 2},
    },
    {
        "name": "merged_stacked_blocks_absorbed_by_one_box",
        # 上下叠放的两块：外框与第一块 1-1 就 >0.5，第二块也要被并进来（test 集实况）
        "gt": [[0.087, 0.083, 0.476, 0.308], [0.079, 0.314, 0.480, 0.418]],
        "pred": [[0.087, 0.085, 0.475, 0.405]],
        "want": {"tp": 1, "fp": 0, "fn": 1,
                 "merged_tp": 2, "merged_fp": 0, "merged_fn": 0},
    },
    {
        "name": "merged_mixed_matched_merged_unmatched",
        "gt": [[0.1, 0.1, 0.3, 0.3], [0.4, 0.1, 0.6, 0.3],
               [0.1, 0.6, 0.3, 0.8], [0.7, 0.1, 0.9, 0.3]],
        "pred": [[0.1, 0.6, 0.3, 0.8], [0.1, 0.1, 0.6, 0.3],
                 [0.7, 0.6, 0.9, 0.8]],
        "want": {"tp": 1, "fp": 2, "fn": 3,
                 "merged_tp": 3, "merged_fp": 1, "merged_fn": 1},
    },
]


def run_case(case, tmp):
    gt = {"set": "selftest", "definition": "graphic-only", "pages": [{"id": "p1", "boxes": case["gt"]}]}
    gtp = os.path.join(tmp, f"{case['name']}.gt.json")
    with open(gtp, "w", encoding="utf-8") as fh:
        json.dump(gt, fh)
    pp = os.path.join(tmp, f"{case['name']}.pred.jsonl")
    with open(pp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "p1", "boxes": case["pred"]}) + "\n")
    out = subprocess.run([sys.executable, SCORE, "--gt", gtp, "--pred", pp],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            got = run_case(case, tmp)
            for k, v in case["want"].items():
                if got[k] != v:
                    failures.append(f"{case['name']}: {k}={got[k]} want {v}")
        # 缺页必须显式报告，不能静默当成 0 条预测
        gt = {"set": "selftest", "definition": "graphic-only",
              "pages": [{"id": "p1", "boxes": []}, {"id": "p2", "boxes": [[0.1, 0.1, 0.5, 0.5]]}]}
        gtp = os.path.join(tmp, "missing.gt.json")
        with open(gtp, "w", encoding="utf-8") as fh:
            json.dump(gt, fh)
        pp = os.path.join(tmp, "missing.pred.jsonl")
        with open(pp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "p1", "boxes": []}) + "\n")
        out = subprocess.run([sys.executable, SCORE, "--gt", gtp, "--pred", pp],
                             capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
        if got["missing_pages"] != ["p2"]:
            failures.append(f"missing_pages={got['missing_pages']} want ['p2']")
        if got["fn"] != 1:
            failures.append(f"missing page should count as FN, got fn={got['fn']}")

    print(json.dumps({"selftest": "ok" if not failures else "FAIL", "failures": failures},
                     ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
