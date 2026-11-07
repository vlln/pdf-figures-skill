#!/usr/bin/env python3
"""render_trajectory — 把 events.jsonl 渲染成可读的 HTML，用来复盘一次 run。

为什么不复用 DSH 会话日志：那是 dsh 的私有格式（每行一个 zstd 帧，还有 producer
校验），逆向风险高。events.jsonl 是我们自己留下的完整事件流，渲染成静态页面更稳。

  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/pilot/render_trajectory.py --runs e1-minimal e2b-ruler e4-review
  # -> .work/pilot/trajectories/<run>.html

页面里每个 step 折叠一次，展开能看到思考文本、每条命令、每条返回的完整内容。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PILOT = os.path.join(ROOT, ".work", "pilot")

CSS = """
body{font:14px/1.55 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
     margin:0;padding:24px 32px;color:#1c1c1e;background:#fafafa}
h1{font-size:20px;margin:0 0 4px}
.meta{color:#666;margin-bottom:18px}
nav{margin:0 0 20px;padding:10px 14px;background:#fff;border:1px solid #e3e3e6;border-radius:8px}
nav a{color:#0b62d6;text-decoration:none;margin-right:14px;white-space:nowrap}
details{background:#fff;border:1px solid #e3e3e6;border-radius:8px;margin:0 0 10px;overflow:hidden}
summary{cursor:pointer;padding:9px 13px;font-weight:600;background:#f4f4f6}
summary .t{font-weight:400;color:#666;margin-left:10px}
.step{padding:0 13px 12px}
.think{padding:8px 11px;background:#f0f6ff;border-left:3px solid #9cc3f5;border-radius:4px;
       margin:10px 0;white-space:pre-wrap;color:#2b3a4a}
.call{margin:10px 0;border:1px solid #e6e6ea;border-radius:6px;overflow:hidden}
.hd{padding:5px 10px;background:#eef0f4;font-weight:600;font-size:13px}
pre{margin:0;padding:9px 11px;background:#fbfbfc;overflow-x:auto;font:12px/1.45 ui-monospace,Menlo,monospace;
    white-space:pre-wrap;word-break:break-word;max-height:460px}
pre.out{background:#f7f9f7;color:#22432c;border-top:1px solid #e6e6ea}
.final{padding:12px 14px;background:#eefaf0;border-left:3px solid #4aa96c;border-radius:4px;
       margin:10px 0;white-space:pre-wrap}
.err{color:#a4232b;font-weight:600}
"""


def esc(x) -> str:
    return html.escape(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, indent=2))


def load(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def render_run(run: str) -> dict:
    run_dir = os.path.join(PILOT, run)
    cfg = json.load(open(os.path.join(run_dir, "config.json"), encoding="utf-8"))
    summary = {}
    sp = os.path.join(run_dir, "summary.json")
    if os.path.exists(sp):
        summary = json.load(open(sp, encoding="utf-8"))

    nav, bodies = [], []
    for sdir in sorted(glob.glob(os.path.join(run_dir, "sessions", "*"))):
        rec = json.load(open(os.path.join(sdir, "session.json"), encoding="utf-8"))
        evpath = os.path.join(sdir, "events.jsonl")
        if not os.path.exists(evpath):
            continue
        events = load(evpath)
        sid = f"s{rec['session']:02d}"
        nav.append(f'<a href="#{sid}">{sid} · {esc(rec["pages"][0].split("_o.O-")[-1][:34])}'
                   f' · {rec.get("sec", 0):.0f}s</a>')

        rows, step, think, calls, pend = [], None, [], [], {}
        final = None

        def flush():
            nonlocal step, think, calls, pend
            if step is None:
                return
            body = ""
            if think:
                body += f'<div class="think">{esc("\n".join(think))}</div>'
            for c in calls:
                res = pend.get(c.get("callId"), "")
                body += ('<div class="call"><div class="hd">'
                         f'{esc(c.get("tool"))} — {esc((c.get("input") or {}).get("description", ""))}'
                         f'</div><pre>{esc((c.get("input") or {}))}</pre>'
                         f'<pre class="out">{esc(res[:4000])}</pre></div>')
            n = len(calls)
            rows.append(f'<details><summary>step {step}'
                        f'<span class="t">{n} 次工具调用</span></summary>'
                        f'<div class="step">{body}</div></details>')
            step, think, calls, pend = None, [], [], {}

        for e in events:
            k = e.get("type")
            if k == "status" and e.get("phase") == "step_start":
                flush()
                step = e.get("step")
            elif k == "thinking":
                think.append(e.get("text") or "")
            elif k == "text":
                think.append(e.get("text") or "")
            elif k == "tool_call":
                calls.append(e)
            elif k == "tool_result":
                r = e.get("result")
                pend[e.get("callId")] = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
            elif k == "final":
                final = e.get("text")
        flush()

        prompt = ""
        pp = os.path.join(sdir, "prompt.txt")
        if os.path.exists(pp):
            prompt = open(pp, encoding="utf-8").read()
        head = (f'<h2 id="{sid}">{sid}</h2>'
                f'<div class="meta">{esc(rec.get("pages"))} · {rec.get("sec")}s · '
                f'{rec.get("steps")} steps · {rec.get("tool_calls")} tools · '
                f'{esc(rec.get("model"))}'
                + (f' · <span class="err">problems: {esc(rec.get("problems"))}</span>'
                   if rec.get("problems") else "") + '</div>'
                f'<details><summary>提示词<span class="t">{len(prompt)} 字</span></summary>'
                f'<div class="step"><pre>{esc(prompt)}</pre></div></details>')
        bodies.append(head + "".join(rows)
                      + (f'<div class="final"><b>最终答案</b>\n{esc(final)}</div>' if final else ""))

    title = f"{run} — {cfg.get('arm')} / review={cfg.get('review')} / mosaic={cfg.get('mosaic')}"
    stat = ""
    if summary:
        stat = (f'<div class="meta">F1 {summary.get("f1")} · 合并 {summary.get("merged_f1")} · '
                f'P {summary.get("precision")} · R {summary.get("recall")} · '
                f'{summary.get("sec_per_page")}s/页 · {summary.get("steps_per_page")} 步/页</div>')
    page = (f'<!doctype html><meta charset="utf-8"><title>{esc(title)}</title>'
            f'<style>{CSS}</style><h1>{esc(title)}</h1>{stat}'
            f'<nav>{nav and " ".join(nav)}</nav>') + "".join(bodies)
    out_dir = os.path.join(PILOT, "trajectories")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{run}.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    return {"run": run, "out": out, "sessions": len(bodies), "bytes": len(page)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    a = ap.parse_args()
    made = [render_run(r) for r in a.runs]
    idx = os.path.join(PILOT, "trajectories", "index.html")
    links = " ".join(f'<li><a href="{os.path.basename(m["out"])}">{esc(m["run"])}</a>'
                     f'<span class="t"> — {m["sessions"]} 个会话</span></li>' for m in made)
    with open(idx, "w", encoding="utf-8") as fh:
        fh.write(f'<!doctype html><meta charset="utf-8"><title>pilot trajectories</title>'
                 f'<style>{CSS}</style><h1>小样本实验轨迹</h1><ul>{links}</ul>')
    print(json.dumps({"index": idx, "runs": made}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
