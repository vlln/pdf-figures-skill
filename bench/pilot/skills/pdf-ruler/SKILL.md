---
name: pdf-ruler
description: >
  Use when you need to locate figures, tables, or other regions on a PDF page or
  page image and report coordinates — renders a page or region with a pixel ruler,
  draws candidate boxes back onto the image, packs several targets into one view,
  and converts readings to normalized 0..1 coordinates.
---

# PDF Ruler

```bash
export _S="/absolute/path/to/this/skill"
```

`view` composes four switches — 取图、刻度、画框、打包 — in one call:

```bash
# 整页 + 刻度
python3 $_S/scripts/ruler.py view doc.pdf --ruler --out p.png

# 取一块并放大（页号前缀可选）
python3 $_S/scripts/ruler.py view doc.pdf --box 3:840,100,1654,900 --scale -1 --ruler --out z.png

# 把候选框画回整页
python3 $_S/scripts/ruler.py view doc.pdf --boxes "840,100,1654,900;60,1000,1520,1300" --ruler --out c.png

# 先裁到这些框的并集，再画
python3 $_S/scripts/ruler.py view doc.pdf --boxes "840,100,1654,900;60,1000,1520,1300" --fit union --out u.png

# 打包：整页 1–3，或跨页各取一块
python3 $_S/scripts/ruler.py view doc.pdf --pack "1:;2:;3:" --out three.png --map m1.json
python3 $_S/scripts/ruler.py view doc.pdf --pack "1:840,100,1654,900;3:60,1000,1520,1300" --out two.png --map m2.json
```

`norm` 把图上的读数换算成整页归一化坐标：

```bash
python3 $_S/scripts/ruler.py norm doc.pdf --origin 840,100 --boxes "0,0,300,200"
python3 $_S/scripts/ruler.py norm doc.pdf --map m2.json --boxes "1: 0,40,600,780; 2: 20,10,1400,280"
```

## view 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | — | PDF 或页面图片 |
| `--out` | 必填 | 输出图片 |
| `--page` | `1` | 默认页号；条目没写页号时用它 |
| `--box` | 空 | 取一块：`x0,y0,x1,y1`，可写 `3:x0,y0,x1,y1` |
| `--scale` | `0` | `0` 不放大；负数按 `--target` 放大 |
| `--target` | `1200` | 自动缩放的目标宽度（像素） |
| `--ruler` | 关 | 叠加刻度 |
| `--step` | `0` | 刻度间隔（原图像素），`0` 自动 |
| `--boxes` | 空 | 画框：整页像素，分号分隔，可带页号前缀 |
| `--fit` | `none` | `union` 时先裁到 `--boxes` 的并集 |
| `--pack` | 空 | 打包：每项 `x0,y0,x1,y1`，只写页号表示整页 |
| `--gap` | `26` | 打包块间缝隙（像素） |
| `--max-width` | `2000` | 打包画布最大宽度 |
| `--map` | 空 | 打包时写出各块页号/原点，供 `norm --map` |

`--box` 与 `--pack` 互斥。`norm` 的 `--boxes` 用 `--map` 时写成 `块号:x0,y0,x1,y1`。

## 坐标

- 你给的所有框都是**整页像素**。
- 图上的刻度是**本视图左上角起算的原图像素**：角上是 `0,0`，放大不变。
- `--boxes` 画出的每个框，标签里写着它自己的页面坐标，不必自己加偏移。
- 换算回整页用 `norm --origin`（单视图）或 `norm --map`（打包）。

## 打包

**有多个目标需要进一步细看时就用它** —— 同一页的不同区域、不同页、整页，都算，混着打包也可以。

```bash
# 第一遍扫出三个候选，三个都想确认边界
view doc.pdf --pack "840,100,1654,900;60,1000,1520,1300;80,1350,900,1700" --out t.png --map m.json

# 多页 PDF 想一起看第 2、5、7 页
view doc.pdf --pack "2:;5:;7:" --out pages.png --map m2.json

# 跨页：A 页一张图 + B 页一张表
view doc.pdf --pack "1:840,100,1654,900;3:60,1000,1520,1300" --out x.png --map m3.json
```

每块独立成格、各有自己 `0,0` 的刻度，块间留缝隙；`norm --map` 换算回各自的页。

唯一的代价：一次放 k 块，图像预算要分给 k 块，每块的细节比单独放大少。
块框得越紧、数量越少，看到得越清楚；整页打包时每页分到的像素最少。

## 注意

- PDF 一律按 200 dpi 渲染，像素坐标在各子命令之间一致。
- 页面图片按其自身像素，不做换算。
- `norm` 会把越界的框裁到页面内；标签贴边缘说明真实边界在页面外。
