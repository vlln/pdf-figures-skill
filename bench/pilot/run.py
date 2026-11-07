#!/usr/bin/env python3
"""run — 小样本对照实验的统一运行器（实验 1–5 共用）。

一次调用 = 一个实验条件跑完整个小样本：
  --arm minimal|ruler|pixels   基础配置（无技能 / 尺子技能 / 像素技能）
  --review                     追加「提交前复审」要求
  --mosaic K                   K 页拼成一张大页面，一个会话处理（缺省 1 = 单目标）

隔离：
  - 每个会话一个全新目录 ~/.local/share/pdfx-pilot/runs/r-<随机>/，work/ 里只有 input.pdf。
  - DSH_AGENTS_HOME 指向该目录下的 agents/，只装本组的技能（minimal 为空）；
    因此 ~/.agents 下的全局技能与 AGENTS.md 对所有组都不可见。
  - 整个 dsh 进程套 macOS Seatbelt：只允许读写本会话目录，评测数据、仓库、其他会话不可读。
    会话日志、附件、TMPDIR 都改到本会话目录里。见 sandbox_profile()。
  - 所有组共用同一个 Python 环境（PATH 前置 ~/.local/share/pdfx-pilot/env/bin，在仓库外）。
  - 运行后仍扫描工具调用（leak_suspect）并统计被拦下的次数（sandbox_denials）。

效率指标直接取 `dsh headless --json` 的事件流：step_start 计步数、tool_call 计工具、
step_end.usage 计 token；墙钟由本脚本计时。

用法（P=~/.local/share/pdfx-pilot/env/bin/python）：
  $P bench/pilot/run.py --arm minimal --name e1-minimal
  $P bench/pilot/run.py --arm ruler --name e2-ruler --dry-run
  $P bench/pilot/run.py --arm minimal --name oracle --oracle
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.dirname(HERE)
ROOT = os.path.dirname(BENCH)
SKILLS = {"minimal": None, "ruler": "pdf-ruler", "pixels": "pdf-pixels", "prod": "pdf-figures"}
# 生产技能不在 pilot 目录下，单独指路；其余仍按 bench/pilot/skills/<name> 取
SKILL_SRC = {"pdf-figures": os.path.join(ROOT, "skills", "pdf-figures")}
DATA_DPI = 200            # OmniDocBench 页面图约为 A4@200dpi；PDF 页面尺寸按此换算
# 环境与会话目录都在仓库与评测数据之外，Agent 看不到它们的位置
PILOT_HOME = os.path.expanduser("~/.local/share/pdfx-pilot")
ENV_BIN = os.path.join(PILOT_HOME, "env", "bin")
RUNS_ROOT = os.path.join(PILOT_HOME, "runs")
CONTROL_ROOT = os.path.join(PILOT_HOME, "control")
LABELS = "ABCDEFGHIJKLMNOP"
GAP, HEADER = 60, 90      # 拼图：页间空白、页上方编号带（像素）


# ------------------------------------------------------------------ 输入构造


def write_pdf(path: str, jpeg: bytes, w: int, h: int) -> None:
    """单页 PDF，页面就是这张 JPEG（DCTDecode 直接嵌入，不重新编码）。"""
    pw, ph = w * 72.0 / DATA_DPI, h * 72.0 / DATA_DPI
    content = f"q {pw:.4f} 0 0 {ph:.4f} 0 0 cm /Im0 Do Q".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.4f} {ph:.4f}] ".encode()
         + b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} ".encode()
         + b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
         + str(len(jpeg)).encode() + b" >>\nstream\n" + jpeg + b"\nendstream"),
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    with open(path, "wb") as fh:
        fh.write(out)


def page_pdf(src: str, dst: str) -> tuple[int, int]:
    with Image.open(src) as im:
        w, h = im.size
        ok = im.format == "JPEG" and im.mode == "RGB"
        if not ok:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=95)
    jpeg = open(src, "rb").read() if ok else buf.getvalue()
    write_pdf(dst, jpeg, w, h)
    return w, h


def mosaic_pdf(srcs: list[str], dst: str, png_copy: str | None = None):
    """K 页拼成一张大页面。返回每页的 cell（相对大页面的归一化框）。"""
    ims = [Image.open(s).convert("RGB") for s in srcs]
    k = len(ims)
    cols = 1 if k == 1 else 2
    rows = (k + cols - 1) // cols
    cw, ch = max(i.size[0] for i in ims), max(i.size[1] for i in ims)
    W = cols * cw + (cols + 1) * GAP
    H = rows * (ch + HEADER) + (rows + 1) * GAP
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=64)
    except TypeError:  # 旧版 Pillow
        font = ImageFont.load_default()
    cells = []
    for idx, im in enumerate(ims):
        r, c = divmod(idx, cols)
        x = GAP + c * (cw + GAP)
        y = GAP + r * (ch + HEADER + GAP)
        d.text((x, y + 8), f"Page {LABELS[idx]}", fill=(200, 0, 0), font=font)
        top = y + HEADER
        canvas.paste(im, (x, top))
        d.rectangle([x - 2, top - 2, x + im.size[0] + 1, top + im.size[1] + 1], outline=(160, 160, 160), width=2)
        cells.append([x / W, top / H, (x + im.size[0]) / W, (top + im.size[1]) / H])
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    write_pdf(dst, buf.getvalue(), W, H)
    if png_copy:
        canvas.save(png_copy)
    return cells


def verify_pdf(pdf: str, want_wh: tuple[int, int]) -> dict:
    import pypdfium2
    bm = pypdfium2.PdfDocument(pdf)[0].render(scale=DATA_DPI / 72.0).to_pil()
    return {"render_px": list(bm.size), "source_px": list(want_wh),
            "ok": abs(bm.size[0] - want_wh[0]) <= 1 and abs(bm.size[1] - want_wh[1]) <= 1}


# ------------------------------------------------------------------ 坐标


def cell_to_page(box, cell):
    """大页面归一化框 -> 该页自身的归一化框（裁到 0..1）。"""
    cx0, cy0, cx1, cy1 = cell
    sx, sy = cx1 - cx0, cy1 - cy0
    out = [(box[0] - cx0) / sx, (box[1] - cy0) / sy, (box[2] - cx0) / sx, (box[3] - cy0) / sy]
    return [round(max(0.0, min(1.0, v)), 4) for v in out]


def page_to_cell(box, cell):
    cx0, cy0, cx1, cy1 = cell
    sx, sy = cx1 - cx0, cy1 - cy0
    return [cx0 + box[0] * sx, cy0 + box[1] * sy, cx0 + box[2] * sx, cy0 + box[3] * sy]


def inside(box, cell) -> bool:
    mx, my = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return cell[0] <= mx <= cell[2] and cell[1] <= my <= cell[3]


# ------------------------------------------------------------------ 答案解析


def json_objects(text: str):
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                yield obj
            i = text.find("{", max(end, i + 1))
        except json.JSONDecodeError:
            i = text.find("{", i + 1)


def parse_answer(text: str, labels: list[str]):
    """返回 ({label: [box,...]}, problems)。

    严格：不猜单位、不改写坐标。唯一放宽的是**页面键**——模型偶尔把第 1 页写成 "1"（或 "0"）而不是 "A"；
    这纯粹是答题格式而不是定位能力，按位置接受，但记一条 numeric_label 问题留痕。
    """
    problems = []
    found = None
    for obj in reversed(list(json_objects(text or ""))):
        if isinstance(obj.get("pages"), dict):
            found = obj["pages"]
            break
    if found is None:
        return None, ["no_pages_json"]
    out = {}
    for idx, lab in enumerate(labels, 1):
        boxes = found.get(lab)
        if boxes is None and str(idx) in found and lab not in found:
            boxes = found[str(idx)]
            problems.append(f"numeric_label:{idx}->{lab}")
        # 单页时模型偶尔用 0 起编号（"0" 而不是 "A"）；仅当 1 起编号也不存在时接受
        if boxes is None and idx == 1 and "0" in found and lab not in found and str(1) not in found:
            boxes = found["0"]
            problems.append(f"numeric_label:0->{lab}")
        if boxes is None:
            problems.append(f"missing_label:{lab}")
            boxes = []
        clean = []
        for b in boxes if isinstance(boxes, list) else []:
            if not (isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)):
                problems.append(f"bad_box:{lab}")
                continue
            if any(v < -0.01 or v > 1.01 for v in b):
                problems.append(f"out_of_range:{lab}")  # 多半是像素单位；照实计分，不救
            x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in b]
            if x1 > x0 and y1 > y0:
                clean.append([x0, y0, x1, y1])
            else:
                problems.append(f"empty_box:{lab}")
        out[lab] = clean
    extra = sorted(set(found) - set(labels) - {str(i) for i in range(1, len(labels) + 1)})
    if extra:
        problems.append("unknown_labels:" + ",".join(map(str, extra)))
    return out, problems


# ------------------------------------------------------------------ 事件统计


def event_stats(events: list[dict]) -> dict:
    steps = 0
    tools = collections.Counter()
    usage = collections.Counter()
    session_id, final, end_reason = None, None, None
    for e in events:
        t = e.get("type")
        if t == "session":
            session_id = e.get("sessionId")
        elif t == "status" and e.get("phase") == "step_start":
            steps += 1
        elif t == "status" and e.get("phase") == "step_end":
            for k, v in (e.get("usage") or {}).items():
                if isinstance(v, (int, float)):
                    usage[k] += v
        elif t == "status" and e.get("phase") == "turn_end":
            end_reason = (e.get("reason") or {}).get("kind")
        elif t == "tool_call":
            tools[e.get("tool") or "?"] += 1
        elif t == "final":
            final = e.get("text")
    return {"session_id": session_id, "steps": steps, "tool_calls": sum(tools.values()),
            "tools": dict(tools), "usage": dict(usage), "end_reason": end_reason, "final": final}


def session_model(session_id: str | None, root: str) -> str | None:
    if not session_id or not os.path.isdir(root):
        return None
    for d in os.listdir(root):
        p = os.path.join(root, d, session_id, "session.v4.jsonl.zstd")
        if os.path.exists(p):
            raw = subprocess.run(["zstd", "-dc", p], capture_output=True, text=True).stdout
            seen = collections.Counter()
            for line in raw.splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stack = [e]
                while stack:
                    x = stack.pop()
                    if isinstance(x, dict):
                        if isinstance(x.get("model"), str) and isinstance(x.get("provider"), str) \
                                and "title" not in x["provider"]:
                            seen[f"{x['provider']}/{x['model']}"] += 1
                        stack.extend(x.values())
                    elif isinstance(x, list):
                        stack.extend(x)
            return ",".join(sorted(seen)) or None
    return None


def leak_scan(events: list[dict], needles: list[str]) -> list[str]:
    hits = []
    for e in events:
        if e.get("type") != "tool_call":
            continue
        blob = json.dumps(e.get("input"), ensure_ascii=False)
        for n in needles:
            if n and n in blob:
                hits.append(f"{e.get('tool')}:{n}")
    return sorted(set(hits))


def sandbox_denials(events: list[dict]) -> int:
    """被 Seatbelt 拦下的工具结果数：说明 Agent 试图读写会话目录之外的东西。"""
    return sum(1 for e in events if e.get("type") == "tool_result"
               and "Operation not permitted" in json.dumps(e.get("result"), ensure_ascii=False))


# ------------------------------------------------------------------ 主流程


def build_prompt(arm: str, review: bool, labels: list[str], cells) -> str:
    def read(name):
        with open(os.path.join(HERE, "prompts", name), "r", encoding="utf-8") as fh:
            return fh.read()

    mosaic = ""
    if cells is not None:
        lines = "\n".join(f"- {lab}: " + ",".join(f"{v:.4f}" for v in cell) for lab, cell in zip(labels, cells))
        mosaic = "\n" + read("mosaic.txt").replace("{n}", str(len(labels))).replace("{cells}", lines)
    skill = "\n" + read("skill.txt").replace("{skill_name}", SKILLS[arm]) if SKILLS[arm] else ""
    rev = "\n" + read("review.txt") if review else ""
    keys = "{" + ", ".join(f'"{lab}": [[x0,y0,x1,y1], ...]' for lab in labels) + "}"
    task = read("task.txt")
    if cells is not None:
        task = task.replace("相对整张页面（input.pdf 这一页的全部范围）", "相对整张大页面（input.pdf 这一页的全部范围）")
    return (task.replace("{mosaic}", mosaic).replace("{skill}", skill)
            .replace("{review}", rev).replace("{keys}", keys))


def install_skill(arm: str, agents_home: str) -> None:
    os.makedirs(os.path.join(agents_home, "skills"), exist_ok=True)
    name = SKILLS[arm]
    if not name:
        return
    dst = os.path.join(agents_home, "skills", name)
    src = SKILL_SRC.get(name, os.path.join(HERE, "skills", name))
    shutil.copytree(src, dst)
    md = os.path.join(dst, "SKILL.md")
    with open(md, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(text.replace("/absolute/path/to/this/skill", dst))


def sandbox_profile(run_dir: str) -> str:
    """macOS Seatbelt：只允许读写本会话目录；评测数据、仓库、其他会话、旧临时目录一律不可读。"""
    home = os.path.expanduser("~")
    sys_tmp = os.path.realpath(tempfile.gettempdir())
    denied = [os.path.join(home, "References"), os.path.join(home, "Projects"),
              os.path.join(home, ".agents"), os.path.join(home, ".dsh", "sessions"),
              os.path.join(home, ".dsh", "attachments"), os.path.join(home, ".dsh-ruler"),
              "/private/tmp", sys_tmp, RUNS_ROOT, CONTROL_ROOT]
    paths = " ".join(f'(subpath "{p}")' for p in denied)
    return ("(version 1)\n(allow default)\n"
            f"(deny file-read-data {paths})\n(deny file-write* {paths})\n"
            f'(allow file-read-data (subpath "{run_dir}"))\n(allow file-write* (subpath "{run_dir}"))\n'
            # 附件存储会逐级打开祖先目录做 fsync；只放行 runs 目录项本身（能看到其他会话的随机名，
            # 看不到内容）
            f'(allow file-read-data (literal "{RUNS_ROOT}"))\n')


def run_dsh(prompt: str, run_dir: str, timeout: int, extra_patch=()):
    """在 Seatbelt 里跑一次 headless。会话日志、附件、临时文件都落在 run_dir 下。

    DSH 自带的 bash 沙箱在外层 Seatbelt 里无法嵌套，所以把它关掉（danger-full-access），
    由外层统一限制读写。
    """
    cwd, agents_home = os.path.join(run_dir, "work"), os.path.join(run_dir, "agents")
    patch = os.path.join(run_dir, "patch.yml")
    with open(patch, "w", encoding="utf-8") as fh:
        fh.write("- id: session-persistence-jsonl\n  name: '@deepseek-ai/dsh-session-persistence-jsonl'\n"
                 f"  config:\n    root: {os.path.join(run_dir, 'sessions')}\n"
                 "- id: attachment-local\n  name: '@deepseek-ai/dsh-attachment-local'\n"
                 f"  config:\n    dshHome: {os.path.join(run_dir, 'dshhome')}\n"
                 # 所有组一律没有联网工具：e2/e3 曾用 web_search 去找原论文的图，那不属于取图能力
                 "- id: tool-web\n  disabled: true\n")
    # 额外的装配层（例如换模型）：按原样追加在最后，覆盖前面的同名条目
    for path in extra_patch:
        with open(path, encoding="utf-8") as src, open(patch, "a", encoding="utf-8") as dst:
            dst.write("\n" + src.read())
    os.makedirs(os.path.join(run_dir, "tmp"), exist_ok=True)
    os.makedirs(CONTROL_ROOT, exist_ok=True)
    sb = os.path.join(CONTROL_ROOT, os.path.basename(run_dir) + ".sb")
    with open(sb, "w", encoding="utf-8") as fh:
        fh.write(sandbox_profile(run_dir))
    env = dict(os.environ)
    env["DSH_AGENTS_HOME"] = agents_home
    env["DSH_PERMISSION_MODE"] = "danger-full-access"
    env["TMPDIR"] = os.path.join(run_dir, "tmp")
    env["PATH"] = ENV_BIN + os.pathsep + env.get("PATH", "")
    env.pop("VIRTUAL_ENV", None)
    t0 = time.time()
    cmd = ["sandbox-exec", "-f", sb, "dsh", "--profile", "headless", "--patch", patch, "headless", "--json", prompt]
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
    events = []
    for line in (out or "").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events, err or "", time.time() - t0, timed_out, proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(SKILLS))
    ap.add_argument("--name", required=True, help="实验名，决定输出目录 .work/pilot/<name>")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--mosaic", type=int, default=1, help="每张大页面拼几页（1 = 单目标）")
    ap.add_argument("--pilot", default=os.path.expanduser("~/References/pdf-figures-bench/pilot8"))
    ap.add_argument("--timeout", type=int, default=900, help="每页的超时秒数；拼图会话按页数放大")
    ap.add_argument("--dry-run", action="store_true", help="只构造输入与提示词，不调用模型")
    ap.add_argument("--oracle", action="store_true", help="用 GT 伪造答案走完整条链路（应得 F1=1）")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--extra-patch", action="append", default=[],
                    help="追加的装配层 YAML（可重复），例如换模型")
    a = ap.parse_args()

    with open(os.path.join(a.pilot, "manifest.json"), "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    pages = manifest["pages"]
    gt_path = os.path.join(a.pilot, "gt_graphic_only.json")
    with open(gt_path, "r", encoding="utf-8") as fh:
        gt = {p["id"]: p["boxes"] for p in json.load(fh)["pages"]}

    out_dir = os.path.join(ROOT, ".work", "pilot", a.name)
    if os.path.exists(out_dir) and not a.force:
        raise SystemExit(f"{out_dir} exists; choose a new --name or pass --force")
    os.makedirs(os.path.join(out_dir, "sessions"), exist_ok=True)

    k = max(1, a.mosaic)
    groups = [pages[i:i + k] for i in range(0, len(pages), k)]
    needles = [os.path.expanduser("~/References"), "pdf-figures-bench", "pilot8", "gt_graphic",
               "gt_with_caption", "manifest.json", ROOT, os.path.join(HERE, "skills"),
               os.path.expanduser("~/.agents")] + [p["id"] for p in pages]

    config = {"arm": a.arm, "skill": SKILLS[a.arm], "review": a.review, "mosaic": k,
              "pilot": a.pilot, "seed": manifest.get("seed"), "pages": [p["id"] for p in pages],
              "timeout_per_page": a.timeout, "python": os.path.join(ENV_BIN, "python3"),
              "dry_run": a.dry_run, "oracle": a.oracle, "extra_patch": a.extra_patch,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    with open(os.path.join(out_dir, "config.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)

    preds, sessions = {}, []
    for gi, group in enumerate(groups, 1):
        labels = list(LABELS[:len(group)])
        os.makedirs(RUNS_ROOT, exist_ok=True)
        run_dir = tempfile.mkdtemp(prefix="r-", dir=RUNS_ROOT)   # 不透明名，与实验名无关
        work = os.path.join(run_dir, "work")
        agents_home = os.path.join(run_dir, "agents")
        os.makedirs(work)
        install_skill(a.arm, agents_home)
        srcs = [os.path.join(manifest["images_dir"], p["image"]) for p in group]
        pdf = os.path.join(work, "input.pdf")
        sdir = os.path.join(out_dir, "sessions", f"s{gi:02d}")
        os.makedirs(sdir, exist_ok=True)
        if k == 1:
            wh = page_pdf(srcs[0], pdf)
            cells = None
            page_cells = [[0.0, 0.0, 1.0, 1.0]]
        else:
            cells = mosaic_pdf(srcs, pdf, png_copy=os.path.join(sdir, "mosaic.png"))
            page_cells = cells
            with Image.open(os.path.join(sdir, "mosaic.png")) as im:
                wh = im.size
        check = verify_pdf(pdf, wh)
        prompt = build_prompt(a.arm, a.review, labels, cells)
        with open(os.path.join(sdir, "prompt.txt"), "w", encoding="utf-8") as fh:
            fh.write(prompt)

        rec = {"session": gi, "pages": [p["id"] for p in group], "labels": labels,
               "cells": page_cells, "work": work, "agents_home": agents_home, "pdf_check": check}
        if a.dry_run:
            rec["status"] = "dry_run"
        else:
            if a.oracle:
                ans = {lab: [page_to_cell(b, cell) for b in gt[p["id"]]]
                       for lab, p, cell in zip(labels, group, page_cells)}
                events = [{"type": "final", "text": json.dumps({"pages": ans})}]
                err, sec, timed_out, code = "", 0.0, False, 0
            else:
                events, err, sec, timed_out, code = run_dsh(prompt, run_dir, a.timeout * len(group),
                                                            a.extra_patch)
            with open(os.path.join(sdir, "events.jsonl"), "w", encoding="utf-8") as fh:
                for e in events:
                    fh.write(json.dumps(e, ensure_ascii=False) + "\n")
            with open(os.path.join(sdir, "stderr.txt"), "w", encoding="utf-8") as fh:
                fh.write(err[-20000:])
            st = event_stats(events)
            answer, problems = parse_answer(st.pop("final"), labels) if not timed_out else (None, ["timeout"])
            misplaced = 0
            for lab, p, cell in zip(labels, group, page_cells):
                boxes = (answer or {}).get(lab, [])
                keep = []
                for b in boxes:
                    if k > 1 and not inside(b, cell):
                        misplaced += 1
                        continue
                    keep.append(cell_to_page(b, cell) if k > 1 else [round(v, 4) for v in b])
                preds[p["id"]] = keep
            rec.update(st)
            rec.update({"sec": round(sec, 1), "timed_out": timed_out, "exit_code": code,
                        "valid_answer": answer is not None, "problems": problems,
                        "misplaced_boxes": misplaced,
                        "model": None if a.oracle else session_model(st["session_id"],
                                                                     os.path.join(run_dir, "sessions")),
                        "sandbox_denials": sandbox_denials(events),
                        "leak_suspect": leak_scan(events, needles)})
        sessions.append(rec)
        with open(os.path.join(sdir, "session.json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh, ensure_ascii=False, indent=2)
        print(json.dumps({k_: rec.get(k_) for k_ in ("session", "pages", "status", "sec", "steps",
                                                      "tool_calls", "valid_answer", "problems", "leak_suspect")},
                         ensure_ascii=False), file=sys.stderr, flush=True)

    summary = {"name": a.name, "config": config, "sessions": len(sessions)}
    if not a.dry_run:
        pred_path = os.path.join(out_dir, "pred.jsonl")
        with open(pred_path, "w", encoding="utf-8") as fh:
            for p in pages:
                fh.write(json.dumps({"id": p["id"], "boxes": preds.get(p["id"], [])}) + "\n")
        score = json.loads(subprocess.run([sys.executable, os.path.join(BENCH, "score.py"), "--gt", gt_path,
                                           "--pred", pred_path, "--json", os.path.join(out_dir, "score.json")],
                                          capture_output=True, text=True, check=True).stdout)
        n = len(pages)
        tot = lambda key: sum(s.get(key) or 0 for s in sessions)
        tools = collections.Counter()
        for s in sessions:
            tools.update(s.get("tools") or {})
        tokens = collections.Counter()
        for s in sessions:
            tokens.update(s.get("usage") or {})
        summary.update({
            "f1": score["f1"], "precision": score["precision"], "recall": score["recall"],
            "merged_f1": score["merged_f1"], "miou": score["miou"],
            "tp": score["tp"], "fp": score["fp"], "fn": score["fn"],
            "sec_per_page": round(tot("sec") / n, 1), "steps_per_page": round(tot("steps") / n, 2),
            "tool_calls_per_page": round(tot("tool_calls") / n, 2),
            "tools": dict(tools), "tokens_per_page": {k_: round(v / n) for k_, v in tokens.items()},
            "timeouts": sum(1 for s in sessions if s.get("timed_out")),
            "invalid_answers": sum(1 for s in sessions if not s.get("valid_answer")),
            "problems": sorted({p for s in sessions for p in s.get("problems") or []}),
            "leak_suspect_sessions": [s["session"] for s in sessions if s.get("leak_suspect")],
            "sandbox_denials": tot("sandbox_denials"),
            "models": sorted({s.get("model") for s in sessions if s.get("model")}),
        })
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
