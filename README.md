<h1 align="center">pdf-figures</h1>

<p align="center">
  <strong>能看图的模型，能不能把 PDF 里的图和表取出来？</strong><br/>
  一次针对这个问题的实验，以及由实验沉淀出的取图 Skill。
</p>

<p align="center">
  <img src="https://badgen.net/badge/license/MIT/blue" alt="MIT" />
  <img src="https://badgen.net/badge/spec/Agent%20Skills/8257D0" alt="Agent Skills spec" />
  <img src="https://badgen.net/badge/deps/pypdfium2%20%2B%20Pillow/4B8BBE" alt="two wheels" />
</p>

---

## 概述

`pdf-figures` 把 PDF 当纯像素处理：先把页渲染成图，让模型看着图定出坐标，再由脚本按坐标裁剪。

不解析文本层，也不解析矢量结构。扫描件、图片型 PDF、矢量页因此走同一条路径。

## 结果

八页分层样本（学术 / 书籍 / 杂志 × 图 / 表 / 混排 / 多目标），每页独立环境、不联网。

| 组 | 严格 F1 | 合并 F1 | 输出 token/页 |
|---|---|---|---|
| 裸模型 | 0.850 | 1.000 | 10.0k |
| 尺子 | 0.850 | 1.000 | 10.7k |
| 像素算子 | 0.850 | 1.000 | 12.6k |
| 复审 | 0.850 | 1.000 | 11.5k |
| 打包 | 0.850 | 1.000 | 12.2k |
| 生产版技能 | 0.850 | 1.000 | 9.7k |

合并口径下六组都是 22/22。严格口径少 4 个，全部是同一类：GT 把一幅多面板图拆成几个块，而提示词要求一个外框。

## 实验设计

| 组 | 做法 |
|---|---|
| 裸模型 | 不给任何工具，直接看整页出坐标。作为对照。 |
| 尺子 | 页面任意一块都能带刻度查看，刻度单位是页面像素；模型读刻度，换算交给脚本。 |
| 像素算子 | 不给尺子，给算子（渲染、裁剪、描边、连通域），让模型自己量。 |
| 复审 | 交卷前把框画回图上再看一遍，检查漏检和切边。 |
| 打包 | 把多个待查目标拼进一张图，每块保留自己的坐标，省去来回翻页。 |
| 生产版技能 | 上面这些收敛后的版本，也是 `skills/pdf-figures` 交付的形态。 |

使用官方Deepseek V4 Flash 1017，约 190 tok/s。


## 安装

```bash
skit install https://github.com/vlln/pdf-figures-skill/tree/main/skills/pdf-figures
```

克隆到本地后，也可以用 `skit install ./skills/pdf-figures`。

## Skills

| Skill | Description |
|---|---|
| [pdf-figures](skills/pdf-figures/SKILL.md) | 纯像素取图：渲染页面、定位图表、按坐标裁剪与导出。 |

## 用法

```bash
export _S="/path/to/skills/pdf-figures"

python3 $_S/scripts/pdfx.py probe paper.pdf                    # 页数、背景、是否扫描件
python3 $_S/scripts/pdfx.py sheet paper.pdf                    # 全篇缩略图
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --ruler --out p3.png
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --box 120,150,1000,900 --scale 2 --ruler --out z.png
python3 $_S/scripts/pdfx.py norm  paper.pdf --boxes "0,0,300,120" --origin 120,150
python3 $_S/scripts/pdfx.py export paper.pdf out/ spec.json --dpi 300 --tight
```

输入可以是 PDF，也可以是页面图片。

## Requirements

- Python 3.10+
- `pypdfium2`、`Pillow`

## 评测

```bash
python3 bench/acceptance.py    # 硬性闸口
python3 bench/selftest.py      # 评分链路合成验证
```

逐 run 的可用性判定见 [bench/pilot/EVIDENCE.md](bench/pilot/EVIDENCE.md)，完整报告见 [REPORT.md](REPORT.md)。

## License

MIT。评测数据来自 OmniDocBench（CC-BY-4.0）。
