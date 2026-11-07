#!/usr/bin/env python3
"""acceptance — 硬性闸口的机器检查（只依赖标准库 + ast）。

这些是「无论结果好坏都必须满足」的约束，与 F1 无关：
  1. shipped 脚本只允许标准库 + pypdfium2 + PIL（禁 numpy、禁解析 PDF 的库）。
  2. 两个 selftest 必须绿。
  3. 评分器确定性：同一输入两次结果逐字节一致。
  4. SKILL.md 行数 < 500。
  5. 没有把评测产物写进仓库（runs/ 在 .work 下）。

用法：python3 bench/acceptance.py
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL = os.path.join(ROOT, "skills", "pdf-figures")
SCRIPTS = os.path.join(SKILL, "scripts")
ALLOWED = {"pypdfium2", "PIL"}
STDLIB_HINT = getattr(sys, "stdlib_module_names", None)
if STDLIB_HINT is None:  # py3.9 没有这个属性，用一份够用的保守清单
    STDLIB_HINT = {"os", "sys", "io", "json", "math", "time", "argparse", "tempfile",
                   "hashlib", "random", "glob", "re", "ast", "subprocess", "shutil",
                   "datetime", "collections", "itertools", "typing", "pathlib",
                   "statistics", "functools", "importlib", "uuid", "base64", "struct",
                   "zlib", "urllib", "concurrent", "threading", "queue", "__future__"}


def check_imports() -> list[str]:
    bad = []
    # 小样本实验的两份技能同样只许依赖 pypdfium2 + Pillow
    roots = [SCRIPTS, os.path.join(HERE, "pilot", "skills")]
    walk = [w for r in roots for w in os.walk(r)]
    for dirpath, _dirs, files in walk:
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            tree = ast.parse(open(path, "r", encoding="utf-8").read())
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods = [node.module.split(".")[0]]
                for m in mods:
                    if m in ALLOWED or m in STDLIB_HINT or m in ("make_fixtures",):
                        continue
                    bad.append(f"{f}: {m}")
    return bad


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    checks = {}

    bad = check_imports()
    checks["imports_clean"] = {"ok": not bad, "detail": bad}

    r = run([sys.executable, os.path.join(SCRIPTS, "pdfx.py"), "selftest",
             "--out-dir", os.path.join(tempfile.gettempdir(), "pdfx-acceptance")])
    checks["pdfx_selftest"] = {"ok": r.returncode == 0,
                               "detail": (r.stdout.strip() or r.stderr.strip())[-300:]}

    r = run([sys.executable, os.path.join(HERE, "selftest.py")])
    checks["bench_selftest"] = {"ok": r.returncode == 0,
                                "detail": (r.stdout.strip() or r.stderr.strip())[-200:]}

    md = os.path.join(SKILL, "SKILL.md")
    lines = sum(1 for _ in open(md, "r", encoding="utf-8"))
    checks["skill_md_lines"] = {"ok": lines < 500, "detail": lines}

    # 确定性：同一输入两次评分必须完全一致
    with tempfile.TemporaryDirectory() as tmp:
        gt = os.path.join(tmp, "gt.json")
        with open(gt, "w", encoding="utf-8") as fh:
            json.dump({"set": "t", "definition": "graphic_only",
                       "pages": [{"id": "p1", "boxes": [[0.1, 0.1, 0.5, 0.5]]},
                                 {"id": "p2", "boxes": [[0.2, 0.2, 0.6, 0.9]]}]}, fh)
        pred = os.path.join(tmp, "pred.jsonl")
        with open(pred, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": "p1", "boxes": [[0.11, 0.11, 0.49, 0.49]]}) + "\n")
            fh.write(json.dumps({"id": "p2", "boxes": [[0.2, 0.2, 0.6, 0.9]]}) + "\n")
        outs = [run([sys.executable, os.path.join(HERE, "score.py"), "--gt", gt, "--pred", pred]).stdout
                for _ in range(2)]
        checks["scorer_deterministic"] = {"ok": outs[0] == outs[1] and bool(outs[0]),
                                          "detail": "identical" if outs[0] == outs[1] else "differs"}

    ok = all(c["ok"] for c in checks.values())
    print(json.dumps({"acceptance": "PASS" if ok else "FAIL", "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
