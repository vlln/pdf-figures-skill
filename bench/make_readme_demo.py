#!/usr/bin/env python3
"""生成 README 方法示意表用的四张图。

源页是 OmniDocBench（CC-BY-4.0）的一页，见 docs/readme/source.jpg。
四张图都由 pdfx 的真实命令产出，不是手画的：

  尺子    view --box --scale 2 --ruler
  像素算子  ink 掩膜 + 行投影切带
  复审    view --boxes 同时画一个粗框和一个改正框
  打包    view --pack 把两块拼进一张图

  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/make_readme_demo.py
"""

import argparse
import os
import subprocess
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PDFX = os.path.join(ROOT, "skills", "pdf-figures", "scripts", "pdfx.py")

# source.jpg 上的坐标（像素），取自版面标注
FIG1 = (215, 98, 764, 509)
FIG2 = (771, 516, 1321, 938)


def run(*args):
    subprocess.run([sys.executable, PDFX, *args], check=True, capture_output=True, text=True)


def make_ruler(page, out):
    run("view", page, "--out", os.path.join(out, "ruler.png"),
        "--box", "170,60,820,560", "--scale", "1.6", "--ruler", "--step", "50")


def make_review(page, out):
    run("view", page, "--out", os.path.join(out, "review.png"),
        "--box", "150,50,850,580", "--scale", "1.2",
        "--boxes", "195,75,790,540;215,98,764,509")


def make_pack(page, out):
    run("view", page, "--out", os.path.join(out, "pack.png"),
        "--pack", "215,98,764,509;771,516,1321,938", "--scale", "1")


def make_mask(page, out):
    sys.path.insert(0, os.path.dirname(PDFX))
    import pdfx  # noqa: E402

    im = Image.open(page).convert("RGB").crop((170, 60, 820, 560))
    ink = pdfx.ink(im)
    canvas = ink.convert("RGB")
    d = ImageDraw.Draw(canvas)
    bb = ink.getbbox()
    if bb:
        d.rectangle(bb, outline=(0, 120, 255), width=4)
    canvas.save(os.path.join(out, "pixels.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "readme"))
    ap.add_argument("--page", default=os.path.join(ROOT, "docs", "readme", "source.jpg"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    make_ruler(a.page, a.out)
    make_review(a.page, a.out)
    make_pack(a.page, a.out)
    make_mask(a.page, a.out)
    print("written to", a.out)


if __name__ == "__main__":
    main()
