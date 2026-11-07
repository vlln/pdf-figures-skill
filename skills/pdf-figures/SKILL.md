---
name: pdf-figures
description: >
  Use when an agent must pull figures, tables, or charts out of a PDF as images —
  especially when the PDF has no usable text layer or vector structure (scanned
  pages, exported images, slides) or when the figure boundaries are ambiguous.
  Works on pixels only: render page, locate by looking, crop at high DPI, verify.
license: MIT
metadata:
  author: vlln
  version: "0.3.0"
requires:
  bins: []
---

# PDF 取图

把 PDF 当**纯像素**处理：不解析文本层、不解析矢量图元。因此矢量 PDF 与扫描件走同一条路径。

依赖只有两个 wheel：`pypdfium2`（渲染）与 `Pillow`（像素算子）。

```bash
pip install pypdfium2 pillow
export _S="/absolute/path/to/this/skill"
python3 $_S/scripts/pdfx.py <command> ...
```

每条命令输出**一行 JSON**（图片只回路径）。

## 坐标：三种，别混

| 名字 | 范围 | 出现在哪 |
|---|---|---|
| 整页归一化 | 0..1，左上原点 | `norm` 的输出、`export` 的输入 |
| 整页像素 | 200 dpi 渲染下的像素；图片输入则是原生像素 | `view` 的 `--box/--boxes/--pack` 输入 |
| 视图局部像素 | 本视图左上角为 0,0 | `view --ruler` 刻度标签、`norm` 的 `--boxes` 输入 |

`view` 的 JSON 里给出 `page_px`、`origin_px`、`scale`，换算一律用 `norm`，不要自己心算。

## view：一条命令，四个开关

```bash
# 整页，带刻度
python3 $_S/scripts/pdfx.py view in.pdf --out p3.png --page 3 --ruler

# 放大一块（整页像素），带刻度
python3 $_S/scripts/pdfx.py view in.pdf --out z.png --page 3 \
    --box 120,340,980,760 --scale 2 --ruler

# 把猜的框画回去核对（标签写的就是整页坐标）
python3 $_S/scripts/pdfx.py view in.pdf --out chk.png --page 3 \
    --boxes "120,340,980,760;1000,340,1600,700" --ruler
```

| 开关 | 默认 | 含义 |
|---|---|---|
| `--page N` | 1 | 默认页号；条目里可写 `3:x0,y0,x1,y1` 覆盖 |
| `--box x0,y0,x1,y1` | 整页 | 取一块，整页像素 |
| `--scale S` | 0 | 0=原样；负数=按 `--target` 自动放大（上限 6×） |
| `--target W` | 1200 | 自动放大时的目标宽度 |
| `--ruler` | 关 | 叠加刻度，标签是本视图局部像素 |
| `--step P` | 0 | 刻度间隔（整页像素），0=按缩放自动取 1/2/5/10… |
| `--boxes` | 空 | 画框，整页像素，`;` 分隔 |
| `--fit union` | none | 先裁到 `--boxes` 的并集再画 |
| `--pack` | 空 | 打包多块：`x0,y0,x1,y1`，只写页号表示整页 |
| `--gap` / `--max-width` | 26 / 2000 | 打包的块间距与画布上限 |
| `--map` | 空 | 打包时写出 `map.json`，给 `norm --map` 用 |

## norm：换算回整页

```bash
# 单视图：给出视图左上角的整页像素
python3 $_S/scripts/pdfx.py norm in.pdf --boxes "0,0,300,120" --origin 120,340

# 打包图：按块号，各块自带页号与原点
python3 $_S/scripts/pdfx.py norm in.pdf --boxes "1:0,0,300,120;2:10,20,200,180" --map map.json
```

输出 `{"boxes":[{"page":3,"box":[x0,y0,x1,y1]}, ...]}`，坐标已裁到 0..1。

## 什么时候打包

有**多个待进一步观察的目标**时才用（不限于跨页，也可是同一页的几个局部）：
把它们放进一张图，每块保留自己的坐标系，逐块看比来回切页省调用。

```bash
python3 $_S/scripts/pdfx.py view in.pdf --out pack.png \
    --pack "2:100,200,700,600;2:800,200,1400,600;5:120,300,900,900" --map map.json
```

## 辅助命令

| 命令 | 用途 |
|---|---|
| `probe` | 页数、页面尺寸、背景亮度、是否扫描件 |
| `sheet` | 全篇缩略图，先分诊哪几页有图 |
| `strip` | 按行切一条横带，用来定图注边界 |
| `export` | 按归一化框批量重渲染出图（含 manifest） |
| `selftest` | 用合成 PDF 自检渲染/阈值/收缩/行切 |

## 默认路径：一眼过

实测过：让模型自己造工具逐行数像素，F1 0.875、63 秒/页；只让它看一次整页，
F1 0.844、3.25 秒/页。**多干活的边际收益接近零。**

所以默认走最省的那条：一页一张整页图 → 按「只框本体」判断 → 直接给归一化坐标 →
`export` 出图。`view`/`norm` 只在**看不准**的时候用。

省下来的预算花在这些地方才划算：高 DPI 重渲染、命名与 manifest、批量。

## 什么时候升级

| 情况 | 动作 |
|---|---|
| 页面特别密（>6 个图表）或有小图 | `view --box ... --scale 2 --ruler` 再看一次 |
| 同一页有多个目标要细看 | `view --pack` 打包 |
| 底边拿不准（图注几行说不清） | `strip` 切行 |
| 框的位置要核对 | `view --boxes` 把框画回去（标签即坐标） |

升级是例外，不是流程。

## 什么时候不要用这个技能

要整篇 markdown、公式、表格内容时，先用版面解析器（MinerU 之类）。本技能只负责
「把图取出来并且取对」，不做版面结构化。

## 深读（按需）

| 文件 | 什么时候读 |
|---|---|
| `$_S/references/workflow.md` | 处理真实 PDF、需要批量出图 |
| `$_S/references/pixel-ops.md` | 想改阈值/投影的实现细节 |
| `$_S/references/failure-cases.md` | 裁出来不对、框太大/太小、整页变黑 |

## 自检

```bash
python3 $_S/scripts/pdfx.py selftest
```
