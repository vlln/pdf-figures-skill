# 详细流程与库用法

## 取景与放大（从一次自由 run 里学来的）

一个 `view` 管四件事：取图、放大、刻度、画框，都是开关，可以组合：

```bash
# 整页 + 像素刻度（红 x / 蓝 y）
python3 $_S/scripts/pdfx.py view doc.pdf --page 3 --ruler --out page.png

# 按像素框放大 + 叠刻度
python3 $_S/scripts/pdfx.py view doc.pdf --page 3 --box 120,150,1000,900 --scale 1.6 --ruler --out z.png
```

**刻度标签的单位是「整页像素」**（200 dpi 渲染下的像素），但**起点是本视图的左上角**：
换一个裁剪框，同样的 0 会指到页面的另一个位置。所以读数只是「离这个视图左上角多远」，
换算回页面的归一化坐标必须走 `norm`：

```bash
python3 $_S/scripts/pdfx.py norm doc.pdf --boxes "0,0,300,120" --origin 120,150
```

`view` 的 JSON 里给了 `origin_px` 与 `scale`，照抄给 `norm` 即可。


下面是 SKILL.md 里被移出的细节，按需查阅。

## 当现成子命令不够用时：把它当库

固定子命令覆盖不到的情况，**不要自己重写像素逻辑**——直接 import 已经处理好
阈值、反相、坐标的那几个函数，然后按这一页的特点自己组合：

```python
import sys; sys.path.insert(0, "/path/to/skill/scripts")
from pdfx import Doc, ink, profile, runs, gaps, page_threshold, trim

doc = Doc("paper.pdf")          # PDF 或页面图都行
im  = doc.render(0, 200)        # 第 1 页，200 dpi
m   = ink(im)                   # 自适应阈值 + 暗底自动反相，返回 0/255 掩膜
th  = page_threshold(im)        # 这一页实际用的阈值，便于调参
rows = profile(m, "row")        # 行投影（纯 PIL，不需要 numpy）
for y0, y1 in runs(rows, 3):    # 有墨的连续行带
    ...
```

这几行等价于裸模型每页现写的 5–10 个脚本，但阈值、坐标、反相都已经处理过。
自己的分析可以叠加在它上面，不要绕过它——绕过就会重新踩那几个坑
（暗底页整页判墨、扫描噪声毁掉阈值、crop 参数顺序写反）。


## 处理真实 PDF 文件时：定位与出图

上面说的是「看」的流程。如果输入是 PDF 文件、并且最终要落盘成高清图片，才需要下面这些：

### 1. 侦察

```bash
python3 $_S/scripts/pdfx.py probe doc.pdf
```

读每页的 `mode`：`vector` 或 `scan`。扫描件后面渲染时不能先低清再放大（没有矢量可重画），
直接上 250–300 dpi；矢量页可以先低清定位。

### 2. 分诊（多页时）

```bash
python3 $_S/scripts/pdfx.py sheet doc.pdf --dpi 45 --cols 5 --out sheet.png
```

看这张缩略图，只回答「哪几页有图」。不要在这一步定位。

### 3. 定位

```bash
python3 $_S/scripts/pdfx.py view doc.pdf --page 3 --ruler --out p3.png
```

整页先看一眼，带像素刻度。**框一定要比你以为的大**：左右各外扩 3–5%，
底部至少多留 4%。实测模型的框在右缘和下缘系统性偏紧（曾偏 −11.5%），因为图注末行的
右端最不显眼。

### 4. 收缩

```bash
python3 $_S/scripts/pdfx.py crop doc.pdf 3 0.09,0.05,0.95,0.70 --dpi 300 --tight --split \
  --out fig.png
```

返回里必看两项：

- `touch_edge`：任何一边为 `true`，说明你给的框本身切了内容，**放大重来**。这是免费的验收信号。
- `row_gaps` / `col_gaps`：图与图注之间、面板之间的空白带，用来判断该不该拆。

### 5. 边界靠行号，不靠坐标

图注有哪几行是能看清的，边界 y 是说不准的。把边界区域切成带编号的行：

```bash
python3 $_S/scripts/pdfx.py strip doc.pdf 3 0.55 0.72 --out strip.png
```

看图，回答「切到第 N 行」，再把 `lines[N-1].y_end` 代入 `crop` 的 `y1`。

### 6. 验收

每次裁完都要回看图片。检查三件事：没切边、没混进页眉页脚、图注与图对得上。

## 多面板

`--split` 给出 `row_gaps`/`col_gaps` 或 `split` 子命令直接给面板网格。默认导出整图；
只有当用户要单面板时才拆。

## 导出

定位完成后用 `export` 落盘，一次写图 + manifest：

```bash
cat > spec.json <<'EOF'
{"items":[{"page":3,"box":[0.09,0.05,0.95,0.70],"label":"Figure 1"}]}
EOF
python3 $_S/scripts/pdfx.py export doc.pdf out/ spec.json --dpi 300 --tight
```

```
out/
  figures/p003_fig01.png     # 命名带页码，任何一张都能回溯原页与坐标
  manifest.json              # 页码、box、像素尺寸、dpi、label、是否触边
```

## 图注要不要一起框

默认**要**。交付物是「能直接看懂的图」，没有图注的裁图价值很低。
但要注意两种口径的区别，别在验收时自欺：

- 版面块口径（许多数据集的标注）：只框图形本体。
- 交付口径：图形 ∪ 图注。

图注横跨整栏时，带图注的框会明显变宽；如果下游要的是「图形本体」，就再给一个紧框，
两个都导出，别在中间纠结。`crop` 的 `--tight` 会把框收到墨迹边界，`strip` 用来确定图注的结束行。

## 多页文档：一个会话跑整篇

不要一页开一个会话。先用 `sheet` 一次性看清全篇，再只对候选页做精修；
逐页重读说明和重建上下文的开销，比多看两张缩略图贵得多。

## Gotchas

- **右缘与下缘永远偏紧**：这是系统性偏差，不是偶发。默认外扩，别相信自己第一次的框。
- **页眉会被墨迹收缩忠实地卷进来**：墨迹只会说「这里有东西」，不会说「这是噪声」。
  上边界必须用语义判断，或遵守默认页眉带（顶部 7%）排除。
- **扫描页用相对阈值**：固定阈值（如 245）在带噪点的扫描件上会把整页判成墨迹。
  `pdfx` 已按 `背景中位数 − 18` 自适应，不要改成常数。
- **图注可能横跨通栏而图形只占左半**：整框看起来「右边很空」是对的，别为省白边把图注切掉。
- **表格注在图注上方**：同一套 `strip`，只是搜索方向相反。
- **文档无图时**输出 `{"boxes":[]}`，不要硬找。

## 深读

| 文件 | 什么时候读 |
|---|---|
| `$_S/references/pixel-ops.md` | 想改阈值、投影、行条的实现细节时——里面是实测数字 |
| `$_S/references/failure-cases.md` | 裁出来不对、框太大/太小、整页变黑时 |

