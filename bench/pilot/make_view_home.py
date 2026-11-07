#!/usr/bin/env python3
"""make_view_home — 把若干次 pilot run 的会话装进一个独立的 DSH_HOME，用 dsh web 查看。

每个 pilot 会话是在自己的沙箱目录里跑的，日志在
  ~/.local/share/pdfx-pilot/runs/r-<随机>/sessions/--...work--/session-<uuid>/
这里把它们复制进一个可启动的 home，并按「实验 · 分层」命名工作区。

  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/pilot/make_view_home.py --runs e1-minimal e2-ruler e3-pixels
  DSH_HOME=~/.dsh-pilot dsh web --port 3280 --no-open

只读取 pilot 产物，不改动它们。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PILOT = os.path.join(ROOT, ".work", "pilot")
MAIN_HOME = os.path.expanduser("~/.dsh")
TITLES = {"e1-minimal": "E1 minimal", "e2-ruler": "E2 尺子（旧）", "e2b-ruler": "E2b 尺子（新）",
          "e3-pixels": "E3 像素技能", "e4-review": "E4 复审", "e5-mosaic": "E5 整页拼图",
          "e5b-pack": "E5b 打包（Agent 自选）", "e5c-view": "E5c 尺子·两命令",
          "e5d-view": "E5d 尺子·打包说明", "e5d-r2": "E5d-r2 同 E5d 复跑",
          "m2-kimi26-minimal": "M2 kimi-k2.6（无技能）",
          "m1-glm53-INVALID-textonly": "M1 glm-5.3（作废·纯文本）"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--home", default=os.path.expanduser("~/.dsh-pilot"))
    ap.add_argument("--pilot", default=os.path.expanduser("~/References/pdf-figures-bench/pilot8"))
    ap.add_argument("--force", action="store_true", help="清空已有 home 后重建")
    a = ap.parse_args()

    if os.path.exists(a.home):
        if not a.force:
            raise SystemExit(f"{a.home} exists; pass --force to rebuild")
        shutil.rmtree(a.home)
    os.makedirs(os.path.join(a.home, "sessions"))
    os.makedirs(os.path.join(a.home, "storages"))

    # profile 与全局技能沿用本机的；凭据只复制，不新建
    for name in ("profiles", "skills"):
        target = os.path.join(MAIN_HOME, name)
        if os.path.isdir(target):
            os.symlink(target, os.path.join(a.home, name))
    cred = os.path.join(MAIN_HOME, ".credentials.yaml")
    if os.path.exists(cred):
        shutil.copyfile(cred, os.path.join(a.home, ".credentials.yaml"))
        os.chmod(os.path.join(a.home, ".credentials.yaml"), 0o600)

    strata = {}
    with open(os.path.join(a.pilot, "manifest.json"), encoding="utf-8") as fh:
        for p in json.load(fh)["pages"]:
            strata[p["id"]] = p["stratum"]

    workspaces, order, missing = {}, [], []
    for run in a.runs:
        run_dir = os.path.join(PILOT, run)
        if not os.path.isdir(run_dir):
            raise SystemExit(f"no such run: {run_dir}")
        for sdir in sorted(glob.glob(os.path.join(run_dir, "sessions", "*"))):
            rec = json.load(open(os.path.join(sdir, "session.json"), encoding="utf-8"))
            sid = rec.get("session_id")
            # rec["work"] 是 <sandbox run dir>/work，会话日志就在同级的 sessions/ 下
            sandbox = os.path.dirname(rec.get("work", ""))
            found = glob.glob(os.path.join(sandbox, "sessions", "*", sid)) if sid else []
            if not found:
                missing.append(f"{run} s{rec['session']:02d} ({sid})")
                continue
            encoded = os.path.basename(os.path.dirname(found[0]))
            shutil.copytree(os.path.dirname(found[0]), os.path.join(a.home, "sessions", encoded),
                            dirs_exist_ok=True)
            wid = f"wspilot{run.replace('-', '')}{rec['session']:02d}"
            workspaces[wid] = {
                "path": rec["work"],
                "title": (f"{TITLES.get(run, run)} · 拼 {len(rec['pages'])} 页"
                          f"（{strata.get(rec['pages'][0], '?')} … {strata.get(rec['pages'][-1], '?')}）"
                          if len(rec["pages"]) > 1
                          else f"{TITLES.get(run, run)} · {strata.get(rec['pages'][0], '?')}"),
                "sessionIds": [sid],
                "createdAt": "2026-11-05T00:00:00.000Z",
                "updatedAt": "2026-11-05T00:00:00.000Z",
            }
            order.append(wid)

    with open(os.path.join(a.home, "storages", "workspace.json"), "w", encoding="utf-8") as fh:
        json.dump({"unit": {"name": "workspace", "version": 2},
                   "global": {"initialized": True, "workspaceIds": order,
                              "archivedSessionIds": [], "pinnedSessionIds": []},
                   "tables": {"workspaces": workspaces}}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"home": a.home, "sessions": len(order), "missing": missing,
                      "titles": [workspaces[w]["title"] for w in order]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
