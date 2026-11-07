#!/usr/bin/env python3
"""score — 取图评测的官方评分器（只依赖标准库）。

输入：
  gt.json    { "set": "...", "pages": [ {"id": "p001", "boxes": [[x0,y0,x1,y1], ...]}, ... ] }
  pred.jsonl 每行 {"id": "p001", "boxes": [[x0,y0,x1,y1], ...], "meta": {...}}

坐标：归一化 0..1，左上原点。GT 与预测使用同一页尺寸，无需换算。

口径（写进报告时必须原样引用）：
  - 贪心一对一匹配，按 IoU 从高到低，IoU >= thr 判正例。
  - 未匹配预测计 FP，未匹配 GT 计 FN。
  - 反作弊：预测框面积 > 0.85 页面积直接计 FP（否则「整页都框」能刷满召回）。
  - 支持两种 GT 定义：graphic-only 与 graphic+caption，分别存档、分别评分。

用法：
  python3 score.py --gt gt_graphic_only.json --pred runs/x/pred.jsonl --iou 0.5 [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import math
import os

# 反作弊阈值。曾是 0.60，但 test 集里有 4 个真实图框面积 0.60–0.67，
# 完全正确的预测也会被判 FP；改为 0.85，整页框（≈1.0）仍判 FP。
MAX_AREA = 0.85


def iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(0.0, (a[2] - a[0])) * max(0.0, (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0])) * max(0.0, (b[3] - b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def score_page_merged(gts, preds, thr):
    """figure 级口径：允许一个预测框同时覆盖多个 GT 块（多面板图）。

    OmniDocBench 按「图像块」标注，一幅两联图会被标成两个框；而交付物是「一幅图」。
    严格 1-1 匹配会把正确行为判成 1 FP + 2 FN。这里做一次合并匹配。

    合并匹配对**已匹配的预测也生效**：一个外框常常先与其中一块的 1-1 IoU 就超过阈值
    （上下叠放的两块特别容易），若只处理「未被匹配的预测」，第二块会白白记成 FN。
    因此只要预测框确实罩住了另一块、且与并集 IoU 达标，就一并计入。
    """
    tp, _fp, _fn, ious = score_page(gts, preds, thr)
    used_p = set()
    used_g = set()
    # 重跑一次拿到具体配对（与 score_page 同序）
    keep = [p for p in preds if area(p) <= MAX_AREA]
    inflated = len(preds) - len(keep)
    pairs = sorted(((iou(g, p), i, j) for i, g in enumerate(gts) for j, p in enumerate(keep)), reverse=True)
    for v, i, j in pairs:
        if v >= thr and i not in used_g and j not in used_p:
            used_g.add(i); used_p.add(j)
    extra_tp = 0
    for j, pred in enumerate(keep):
        # 已经被这块「并进」的 GT 也算在并集里，否则并集算小了反而不达标
        covered = [i for i, g in enumerate(gts) if iou(g, pred) > 0.15
                   and pred[0] <= (g[0] + g[2]) / 2 <= pred[2] and pred[1] <= (g[1] + g[3]) / 2 <= pred[3]]
        fresh = [i for i in covered if i not in used_g]
        if not fresh or len(covered) < 2:
            continue
        union = [min(gts[i][0] for i in covered), min(gts[i][1] for i in covered),
                 max(gts[i][2] for i in covered), max(gts[i][3] for i in covered)]
        if iou(pred, union) >= thr:
            extra_tp += len(fresh)
            used_g.update(fresh)
            used_p.add(j)
    # FP 只按「最终仍未被用上的预测」计一次，再加上超面积框；不能叠加严格口径的 FP。
    return tp + extra_tp, (len(keep) - len(used_p)) + inflated, len(gts) - len(used_g), ious, extra_tp


def score_page(gts, preds, thr):
    """返回 (tp, fp, fn, ious)。"""
    keep = [p for p in preds if area(p) <= MAX_AREA]
    inflated = len(preds) - len(keep)
    pairs = []
    for i, g in enumerate(gts):
        for j, p in enumerate(keep):
            v = iou(g, p)
            if v >= thr:
                pairs.append((v, i, j))
    pairs.sort(reverse=True)
    used_g, used_p = set(), set()
    ious = []
    for v, i, j in pairs:
        if i in used_g or j in used_p:
            continue
        used_g.add(i)
        used_p.add(j)
        ious.append(v)
    tp = len(ious)
    fn = len(gts) - tp
    fp = len(keep) - tp
    return tp, fp + inflated, fn, ious


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--json", default=None)
    ap.add_argument("--only-pred", action="store_true", help="分母只算预测里出现的页（部分跑时用）")
    a = ap.parse_args()

    with open(a.gt, "r", encoding="utf-8") as fh:
        gt = json.load(fh)
    gt_pages = {p["id"]: p["boxes"] for p in gt["pages"]}

    preds = {}
    with open(a.pred, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            preds[rec["id"]] = rec.get("boxes") or []

    ids = list(gt_pages.keys())
    if a.only_pred:
        ids = [i for i in ids if i in preds]
    missing = [i for i in gt_pages if i not in preds]
    per_page, tp = [], 0
    fp = fn = 0
    mtp = mfp = mfn = 0
    all_ious = []
    for pid in ids:
        t, f, n, ious = score_page(gt_pages[pid], preds.get(pid, []), a.iou)
        tm, fm, nm, _ious_m, extra = score_page_merged(gt_pages[pid], preds.get(pid, []), a.iou)
        tp += t
        fp += f
        fn += n
        mtp += tm
        mfp += fm
        mfn += nm
        all_ious.extend(ious)
        prec = t / (t + f) if t + f else (1.0 if not gt_pages[pid] else 0.0)
        rec = t / (t + n) if t + n else 1.0
        per_page.append({"id": pid, "tp": t, "fp": f, "fn": n,
                         "precision": round(prec, 4), "recall": round(rec, 4),
                         "gt": len(gt_pages[pid])})

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    macro_p = sum(p["precision"] for p in per_page) / len(per_page) if per_page else 0.0
    macro_r = sum(p["recall"] for p in per_page) / len(per_page) if per_page else 0.0
    macro_f1 = 2 * macro_p * macro_r / (macro_p + macro_r) if macro_p + macro_r else 0.0

    # 期望校准误差式的分布信息：IoU 分布能看出「差一点」还是「差很多」
    bins = [0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    hist = [0] * (len(bins) - 1)
    for v in all_ious:
        for k in range(len(bins) - 1):
            if bins[k] <= v < bins[k + 1]:
                hist[k] += 1
                break

    mprec = mtp / (mtp + mfp) if mtp + mfp else 0.0
    mrec = mtp / (mtp + mfn) if mtp + mfn else 0.0
    f1_merged = round(2 * mprec * mrec / (mprec + mrec), 4) if mprec + mrec else None

    report = {
        "set": gt.get("set"),
        "gt_definition": gt.get("definition"),
        "iou_threshold": a.iou,
        "pages": len(ids),
        "missing_pages": missing,
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "macro_precision": round(macro_p, 4),
        "macro_recall": round(macro_r, 4),
        "macro_f1": round(macro_f1, 4),
        "miou": round(sum(all_ious) / len(all_ious), 4) if all_ious else 0.0,
        "merged_tp": mtp, "merged_fp": mfp, "merged_fn": mfn,
        "merged_precision": round(mprec, 4), "merged_recall": round(mrec, 4),
        "merged_f1": f1_merged,
        "iou_hist": {f"{bins[k]}-{bins[k+1]}": hist[k] for k in range(len(hist))},
        "per_page": per_page,
    }

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "per_page"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
