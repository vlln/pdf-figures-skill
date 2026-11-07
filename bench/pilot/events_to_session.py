#!/usr/bin/env python3
"""events_to_session — 把 run.py 留下的 events.jsonl 还原成 DSH 会话日志。

`dsh headless --json` 的事件流里有：会话 id 与 cwd、每个 step 的边界、模型的思考文本、
工具调用与返回。这些正好够拼出 `session.v4.jsonl.zstd` 的骨干，让 GUI 能翻看。

**这是有损重建**：DSH 原始日志里的 request/header、模型 replayState、job 生命周期等记录
无法还原，所以这些会话只能看、不适合在里面继续对话。原始日志还在时不要用这条路。

用法：
  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/pilot/events_to_session.py --run e1-minimal --out ~/.dsh-pilot-events
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PILOT = os.path.join(ROOT, ".work", "pilot")


def load_events(path: str):
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def encode_cwd(cwd: str) -> str:
    """与 dsh 的会话目录命名一致：'/' 换成 '-'，两端各补一个 '-'。

    '/Users/vlln/.local/share/.../work' -> '--Users-vlln-.local-share-...-work--'
    点与下划线保留。
    """
    return "--" + cwd.replace("/", "-").strip("-") + "--"


def build_records(events, prompt: str, session_id: str, cwd: str, model: str | None):
    recs = []
    seq = 0
    t = 1_793_900_000_000

    def add(kind, data):
        nonlocal seq, t
        recs.append({"type": kind, "seq": seq, "time": t, "data": data})
        seq += 1
        t += 7

    recs.append({"type": "session", "version": 4, "id": session_id, "createdAt": t,
                 "cwd": cwd, "isSeeded": False, "delegationDepth": 0})
    t += 7
    add("permission/preset", {"preset": "danger-full-access"})
    add("sandbox/mode", {"mode": "danger-full-access"})
    add("approval/policy", {"policy": "never"})
    add("agent/inbox/spliced", {"target": "next-turn", "start": 0,
                                "inserted": [{"content": [{"type": "text", "text": prompt}]}]})
    add("turn/start", {"turn": 1})

    step, thinking, calls, results, usage = None, [], [], [], {}

    def flush():
        nonlocal step, thinking, calls, results, usage
        if step is None:
            return
        content = []
        if thinking:
            content.append({"type": "reasoning", "text": "\n".join(thinking)})
        for c in calls:
            content.append({"type": "tool-call", "id": c["callId"], "name": c["tool"],
                            "arguments": json.dumps(c["input"], ensure_ascii=False)})
        msg = {"turn": 1, "step": step, "usage": usage or {},
               "message": {"role": "assistant", "id": str(uuid.uuid4()), "content": content,
                           "source": {"kind": "model", "provider": "deepseek-official",
                                      "model": model or "unknown"}}}
        add("assistant/message", msg)
        for c in calls:
            add("tool/call", {"turn": 1, "step": step, "callId": c["callId"], "name": c["tool"],
                              "arguments": json.dumps(c["input"], ensure_ascii=False)})
        for c, r in zip(calls, results):
            add("tool/result", {"turn": 1, "step": step,
                                "message": {"role": "tool", "id": str(uuid.uuid4()),
                                            "source": {"kind": "tool", "callId": c["callId"]},
                                            "toolCallId": c["callId"], "isError": False,
                                            "content": [{"type": "text", "text": r}]}})
        add("step/end", {"turn": 1, "step": step, "usage": usage})
        step, thinking, calls, results, usage = None, [], [], [], {}

    final_text = None
    for e in events:
        k = e.get("type")
        if k == "status" and e.get("phase") == "step_start":
            flush()
            step = e.get("step")
            add("step/start", {"turn": 1, "step": step})
        elif k == "status" and e.get("phase") == "step_end":
            usage = e.get("usage") or {}
        elif k == "thinking":
            thinking.append(e.get("text") or "")
        elif k == "text":
            thinking.append(e.get("text") or "")
        elif k == "tool_call":
            calls.append(e)
        elif k == "tool_result":
            results.append(e.get("result") if isinstance(e.get("result"), str)
                           else json.dumps(e.get("result"), ensure_ascii=False))
        elif k == "final":
            final_text = e.get("text")
    flush()
    if final_text:
        add("assistant/message", {"turn": 1, "step": (step or 1) + 1,
                                  "message": {"role": "assistant",
                                              "content": [{"type": "text", "text": final_text}],
                                              "source": {"kind": "model",
                                                         "provider": "deepseek-official",
                                                         "model": model or "unknown"}}})
    add("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    return recs


def write_session(sdir: str, out_home: str, model: str | None = None) -> dict:
    rec = json.load(open(os.path.join(sdir, "session.json"), encoding="utf-8"))
    prompt = open(os.path.join(sdir, "prompt.txt"), encoding="utf-8").read()
    events = load_events(os.path.join(sdir, "events.jsonl"))
    sid = rec.get("session_id") or f"session-synth-{rec['session']:02d}"
    cwd = rec.get("work")
    recs = build_records(events, prompt, sid, cwd, model or rec.get("model"))
    encoded = encode_cwd(cwd)
    dst = os.path.join(out_home, "sessions", encoded, sid)
    os.makedirs(dst, exist_ok=True)
    # dsh 的会话日志是「每行一个 zstd 帧」拼接，不是整块压缩：
    # 这样单条记录写坏也只坏那一帧，读取时能明确报出 torn record。
    out = os.path.join(dst, "session.v4.jsonl.zstd")
    with open(out, "wb") as fh:
        for r in recs:
            line = (json.dumps(r, ensure_ascii=False) + "\n").encode("utf-8")
            fh.write(subprocess.run(["zstd", "-q", "-c"], input=line, capture_output=True,
                                    check=True).stdout)
    open(os.path.join(dst, "session.lock"), "w").close()
    return {"id": sid, "cwd": cwd, "encoded": encoded, "records": len(recs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=os.path.expanduser("~/.dsh-pilot-events"))
    args = ap.parse_args()
    made = []
    for sdir in sorted(glob.glob(os.path.join(PILOT, args.run, "sessions", "*"))):
        if os.path.exists(os.path.join(sdir, "events.jsonl")):
            made.append(write_session(sdir, args.out))
    print(json.dumps({"run": args.run, "sessions": len(made), "out": args.out,
                      "detail": made[:2]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
