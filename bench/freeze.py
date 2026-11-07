#!/usr/bin/env python3
"""freeze — 给评测集补上每页 sha256，产出可复现的冻结清单（只依赖标准库）。

dsh-evaluation §3 要求钉住媒体 hash 与稳定 ID。数据集下载完成后跑一次，
之后所有 run 都引用这份 manifest，禁止再改。

用法：
  python3 bench/freeze.py --set ~/References/pdf-figures-bench/acad \
      --images ~/References/pdf-figures-bench/omnidocbench/images
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, dest="set_dir")
    ap.add_argument("--images", required=True)
    a = ap.parse_args()

    path = os.path.join(a.set_dir, "manifest.json")
    with open(path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    missing, present = [], 0
    for page in manifest["pages"]:
        src = os.path.join(a.images, page["image"])
        if not os.path.exists(src):
            missing.append(page["id"])
            page["sha256"] = None
            continue
        page["sha256"] = sha256(src)
        page["bytes"] = os.path.getsize(src)
        present += 1

    manifest["frozen_at"] = dt.datetime.now().isoformat(timespec="seconds")
    manifest["present"] = present
    manifest["missing"] = missing
    manifest["page_count"] = {s: sum(1 for p in manifest["pages"] if p["split"] == s)
                              for s in ("dev", "test")}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"present": present, "missing": len(missing),
                      "page_count": manifest["page_count"]}, ensure_ascii=False))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
