#!/usr/bin/env python3
"""session_stats — 从 DSH 会话日志统计一次 run 的效率指标。

用法：python3 bench/session_stats.py --cwd m1-4160 [--latest]
输出：steps / tool calls / images / 各工具次数，供「步数·工具·耗时」对照表使用。
"""
from __future__ import annotations
import argparse, collections, glob, json, os, subprocess

def logs(cwd_sub):
    return sorted(glob.glob(os.path.expanduser(
        f"~/.dsh/sessions/*{cwd_sub}*/session-*/session*.jsonl.zstd")))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--latest", action="store_true")
    a = ap.parse_args()
    files = logs(a.cwd)
    if not files:
        print(json.dumps({"error": "no session found", "cwd": a.cwd}))
        return 1
    f = files[-1] if a.latest else files[0]
    raw = subprocess.run(["zstd", "-dc", f], capture_output=True, text=True).stdout
    steps = 0
    names = collections.Counter()
    for line in raw.splitlines():
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = e.get("type")
        if t == "step/start":
            steps += 1
        elif t == "tool/call":
            names[(e.get("data") or {}).get("name") or "?"] += 1
    print(json.dumps({"session": os.path.basename(os.path.dirname(f)), "steps": steps,
                      "tool_calls": sum(names.values()), "tools": dict(names)}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
