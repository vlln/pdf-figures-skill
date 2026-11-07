#!/usr/bin/env python3
"""生成 README 里那张方法示意表用的图。

页面是脚本画的，不依赖任何数据集：一张 A4 比例的假论文页，含一幅柱状图和一张表。
每张图都由 pdfx 自己产出（view / export），所以图里看到的刻度、框、打包结果就是真实行为。

  P=~/.local/share/pdfx-pilot/env/bin/python
  $P bench/make_readme_demo.py --out docs/readme
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PDFX = os.path.join(ROOT, "skills", "pdf-figures", "scripts", "pdfx.py")
W, H = 1000, 1414


def font(size):
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(name):
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def text_bars(d, x, y, width, lines, gap=14, h=7):
    """用灰条模拟正文，避免依赖字体渲染的排版细节。"""
    for i in range(lines):
        w = width if i % 6 != 5 else int(width * 0.62)
        d.rectangle([x, y + i * gap, x + w, y + i * gap + h], fill=(170, 170, 170))


def make_page(path):
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    d.text((80, 60), "A Study of Something in Something Else", font=font(30), fill=(20, 20, 20))
    d.text((80, 105), "A. Author, B. Author, C. Author", font=font(18), fill=(90, 90, 90))
    d.line([(80, 140), (W - 80, 140)], fill=(120, 120, 120), width=1)

    text_bars(d, 80, 165, 380, 16)
    text_bars(d, 600, 165, 320, 16)

    # 图：柱状图 + 坐标轴 + 图注
    d.rectangle([80, 380, 560, 760], outline=(200, 200, 200), width=1)
    d.line([(130, 700), (520, 700)], fill=(60, 60, 60), width=2)
    d.line([(130, 700), (130, 430)], fill=(60, 60, 60), width=2)
    for i, h in enumerate((90, 160, 120, 210, 150)):
        x = 160 + i * 68
        d.rectangle([x, 700 - h, x + 40, 700], fill=(70, 110, 170))
    d.text((150, 725), "Figure 1: Bars of something.", font=font(16), fill=(40, 40, 40))

    # 表：三线表
    d.rectangle([600, 380, 930, 700], outline=(200, 200, 200), width=1)
    for i in range(5):
        y = 430 + i * 55
        d.line([(620, y), (910, y)], fill=(80, 80, 80), width=2 if i in (0, 4) else 1)
    for i in range(4):
        x = 640 + i * 75
        d.line([(x, 430), (x, 650)], fill=(230, 230, 230), width=1)
    for i in range(4):
        for j in range(4):
            d.rectangle([650 + i * 75, 445 + j * 55, 690 + i * 75, 452 + j * 55], fill=(150, 150, 150))
    d.text((620, 710), "Table 1: Numbers of things.", font=font(16), fill=(40, 40, 40))

    text_bars(d, 80, 800, 380, 20)
    text_bars(d, 600, 745, 320, 22)
    d.text((470, 1340), "7", font=font(16), fill=(120, 120, 120))
    im.save(path)


def run(*args):
    subprocess.run([sys.executable, PDFX, *args], check=True, capture_output=True, text=True)


def make_demos(page, out):
    # 裸模型：整页 + 一个直接给出的框
    run("view", page, "--out", os.path.join(out, "bare.png"),
        "--boxes", "80,380,560,760")

    # 尺子：放大看图，带刻度
    run("view", page, "--out", os.path.join(out, "ruler.png"),
        "--box", "60,360,580,790", "--scale", "2", "--ruler", "--step", "50")

    # 复审：同一页上画出"猜的框"和"改正后的框"，裁到这一区域好看清
    run("view", page, "--out", os.path.join(out, "review.png"),
        "--box", "40,330,960,880", "--scale", "1.3",
        "--boxes", "40,350,600,830;80,380,560,760")

    # 打包：三块拼一张
    run("view", page, "--out", os.path.join(out, "pack.png"),
        "--pack", "80,380,560,760;600,380,930,700",
        "--scale", "1", "--max-width", "1200")

    # 生产版：按坐标导出，紧边
    tmp = tempfile.mkdtemp(prefix="pdfx-demo-")
    spec = os.path.join(os.path.dirname(page), "spec.json")
    with open(spec, "w", encoding="utf-8") as fh:
        fh.write('{"items":[{"page":1,"box":[0.08,0.268,0.56,0.538],"label":"Figure 1"}]}')
    run("export", page, os.path.join(tmp, "final"), spec, "--dpi", "200", "--tight")
    shutil.copyfile(os.path.join(tmp, "final", "figures", "p001_fig01.png"),
                    os.path.join(out, "final.png"))


def make_mask(page, out):
    """像素算子：墨迹掩膜 + 行投影切出的带。"""
    sys.path.insert(0, os.path.dirname(PDFX))
    import pdfx  # noqa: E402

    im = Image.open(page).convert("RGB").crop((60, 360, 580, 790))
    mask = pdfx.ink(im)
    prof = pdfx.profile(mask, "row")
    bands = pdfx.runs(prof, 6)
    canvas = mask.convert("RGB")
    d = ImageDraw.Draw(canvas)
    for y0, y1 in bands:
        d.line([(0, y0), (canvas.width, y0)], fill=(0, 120, 255), width=1)
        d.line([(0, y1), (canvas.width, y1)], fill=(0, 120, 255), width=1)
    canvas.save(os.path.join(out, "pixels.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "readme"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    page = os.path.join(tempfile.mkdtemp(prefix="pdfx-demo-"), "page.png")
    make_page(page)
    make_demos(page, a.out)
    make_mask(page, a.out)
    print("written to", a.out)


if __name__ == "__main__":
    main()
