#!/usr/bin/env python3
"""edge_error — 匹配框的四边误差分布（占页边长的百分比）。

用来检验「坐标读得准不准」，这是 F1@0.5 看不出来的那部分。
"""
from __future__ import annotations
import argparse, json, statistics as st

def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)

ap = argparse.ArgumentParser()
ap.add_argument("--gt", required=True)
ap.add_argument("--pred", required=True)
a = ap.parse_args()
gt = {p["id"]: p["boxes"] for p in json.load(open(a.gt))["pages"]}
pred = {json.loads(l)["id"]: json.loads(l)["boxes"] for l in open(a.pred)}
deltas = []
for pid, boxes in pred.items():
    for p in boxes:
        best = max(((iou(p, g), g) for g in gt.get(pid, [])), default=(0, None))
        if best[0] >= 0.5:
            g = best[1]
            deltas.append([abs(p[0]-g[0]), abs(p[1]-g[1]), abs(p[2]-g[2]), abs(p[3]-g[3])])
if not deltas:
    print(json.dumps({"matched": 0}))
else:
    out = {"matched": len(deltas)}
    for k, name in enumerate(["left", "top", "right", "bottom"]):
        vals = sorted(d[k] for d in deltas)
        out[name] = {"median": round(st.median(vals), 4),
                     "p90": round(vals[int(len(vals)*0.9)], 4),
                     "mean": round(sum(vals)/len(vals), 4)}
    print(json.dumps(out))
