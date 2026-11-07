#!/usr/bin/env python3
"""mineru — A 组（MinerU）的薄适配器：把版面解析结果转成与 B/C 同构的 pred.jsonl。

调用约定来自部署端 `/openapi.json`：
  POST /file_parse   multipart，files=<pdf>，return_content_list=true
响应 `results[0]["content_list"]` 是 JSON 字符串，每项含 type / bbox / page_idx，
bbox 是 0–1000 归一化坐标。

两个必须注意的点：
  1. 这台机器有 http_proxy，172.16.* 会走代理并返回 502。必须显式绕过代理，
     否则会误判成「服务挂了」——本项目因此误判过 9 轮。
  2. MinerU 的 `image` 是图、`table` 是表，其余类型（text/title/header/page_number）不要。

用法：
  python3 bench/mineru.py --health
  python3 bench/mineru.py --set <set> --split test --images <img_dir> --limit 80 --out .work/runs/A
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BASE = os.environ.get("MINERU_API_URL", "http://127.0.0.1:8000").rstrip("/")
KEEP = {"image", "table"}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post_parse(pdf_bytes: bytes, name: str, timeout: int):
    bd = "----MinerUBoundary"
    body = b""
    body += (f"--{bd}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
             "Content-Type: application/pdf\r\n\r\n").encode() + pdf_bytes + b"\r\n"
    for k, v in {"lang_list": "en", "return_md": "false", "return_content_list": "true",
                 "table_enable": "true", "formula_enable": "true"}.items():
        body += f"--{bd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += f"--{bd}--\r\n".encode()
    req = urllib.request.Request(BASE + "/file_parse", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={bd}"},
                                 method="POST")
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def health(timeout=10):
    try:
        with _OPENER.open(BASE + "/health", timeout=timeout) as r:
            return {"url": BASE + "/health", "status": r.status, "ok": r.status == 200,
                    "body": json.loads(r.read().decode() or "{}")}
    except Exception as e:
        return {"url": BASE + "/health", "ok": False, "error": str(e)[:200]}


def boxes_from(resp) -> list[list[float]]:
    results = resp.get("results")
    entry = results[0] if isinstance(results, list) else next(iter(results.values()))
    raw = entry.get("content_list")
    items = raw if isinstance(raw, list) else json.loads(raw or "[]")
    out = []
    for it in items:
        if it.get("type") not in KEEP:
            continue
        b = it.get("bbox")
        if not b or len(b) != 4:
            continue
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v) / 1000.0)) for v in b]
        if x1 > x0 and y1 > y0:
            out.append([round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--set", dest="set_dir")
    ap.add_argument("--split", default="test")
    ap.add_argument("--images")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=".work/runs/A")
    a = ap.parse_args()

    h = health()
    if a.health:
        print(json.dumps(h, ensure_ascii=False))
        return 0 if h["ok"] else 1
    if not h["ok"]:
        os.makedirs(a.out, exist_ok=True)
        json.dump({"system": "A", "status": "blocked", "health": h,
                   "checked_at": dt.datetime.now().isoformat(timespec="seconds")},
                  open(os.path.join(a.out, "status.json"), "w"), ensure_ascii=False, indent=2)
        print(json.dumps({"system": "A", "status": "blocked", "health": h}, ensure_ascii=False))
        return 2

    with open(os.path.join(a.set_dir, "manifest.json"), "r", encoding="utf-8") as fh:
        man = json.load(fh)
    pages = [p for p in man["pages"] if p["split"] == a.split]
    if a.limit:
        pages = pages[: a.limit]

    os.makedirs(a.out, exist_ok=True)
    from inputs import image_to_pdf  # 页面图 → 单页 PDF

    def run(page):
        img = os.path.join(a.images, page["image"])
        pdf_path = os.path.join(a.out, "pdfs", page["id"] + ".pdf")
        os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
        image_to_pdf(img, pdf_path)
        with open(pdf_path, "rb") as fh:
            data = fh.read()
        try:
            resp = _post_parse(data, page["id"] + ".pdf", a.timeout)
            return {"id": page["id"], "boxes": boxes_from(resp),
                    "meta": {"name": "mineru", "ok": True}}
        except Exception as e:
            return {"id": page["id"], "boxes": [],
                    "meta": {"name": "mineru", "ok": False, "error": str(e)[:200]}}

    t0 = dt.datetime.now()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        records = list(ex.map(run, pages))

    pred = os.path.join(a.out, "pred.jsonl")
    with open(pred, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    ok = sum(1 for r in records if r["meta"]["ok"])
    took = (dt.datetime.now() - t0).total_seconds()
    json.dump({"system": "A", "status": "complete", "pages": len(records), "ok": ok,
               "sec_per_page": round(took / max(1, len(records)), 1),
               "checked_at": dt.datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(a.out, "status.json"), "w"), ensure_ascii=False, indent=2)
    print(json.dumps({"pages": len(records), "ok": ok, "sec_per_page": round(took / max(1, len(records)), 1),
                      "out": pred}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
