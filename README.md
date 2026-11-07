<h1 align="center">pdf-figures</h1>

<p align="center">
  <strong>能看图的模型，能不能把 PDF 里的图和表取出来？</strong><br/>
  能。这个仓库既是回答这个问题的实验，也是答案本身：<br/>
  一个纯像素的取图 Skill，不解析文本层、不解析矢量，扫描件与矢量页走同一条路。
</p>

<p align="center">
  <img src="https://badgen.net/badge/license/MIT/blue" alt="MIT" />
  <img src="https://badgen.net/badge/spec/Agent%20Skills/8257D0" alt="Agent Skills spec" />
  <img src="https://badgen.net/badge/deps/pypdfium2%20%2B%20Pillow/4B8BBE" alt="two wheels" />
</p>

---

## 结果

在 8 页分层样本上（学术 / 书籍 / 杂志 × 图 / 表 / 混排 / 多目标），每次运行独立沙箱、禁联网：

| 问题 | 结果 |
|---|---|
| 取得了吗 | 22 个目标全中（合并口径 **22/22**；严格口径 17/22，差的 4 个是"多面板该不该合成一框"的标注冲突） |
| 有多快 | **79 秒/页**（3 次，76–81）；裸模型 99 秒/页（3 次，86–109） |
| 复审有用吗 | 没有。加了复审的框与不加时逐像素重合 |
| 换模型呢 | kimi-k2.6 慢 6.7 倍、4/8 超时；glm-5.3 只收文字，图都进不去 |

## 实验组

| 组 | 做法 | F1（严格 / 合并） | 秒/页 | 说明 |
|---|---|---|---|---|
| e1 | 裸模型 | 0.850 / **1.000** | 99.4 | 3 次，86–109 |
| e2b | 尺子技能 | 0.850 / **1.000** | 90.4 | 单次 |
| e3b | 像素技能 | 0.850 / **1.000** | 93.3 | 单次，干净重跑 |
| e4 | 尺子 + 复审 | 0.850 / **1.000** | 95.7 | 白花时间，框不变 |
| e5 | 四页拼一张 | 作废 | 67.3 | 分辨率除以 4，模型会拆回去 |
| e5b–e5d | 打包开关迭代 | 0.850 / **1.000** | 92.8–132.2 | 措辞只改用量，不改分数 |
| **p1** | **生产版技能** | 0.850 / **1.000** | **79.0** | 3 次，76–81，最快也最稳 |
| m2 | kimi-k2.6 | **0.421** | 666.8 | 4/8 超时；跑完的四页 mIoU 0.96 |

三点值得记住：

- **质量是饱和的**——所有 deepseek 组都是 22/22，这批题分不出方法高低。
- **技能省的是长尾**——8 页里 5 页更快：个别页从 129 秒收敛到 44 秒，简单页反而多花几秒。
- **瓶颈在推理开销**——kimi 定位精度 0.96 mIoU，但一页 11 分钟。

## 安装

### [skit](https://github.com/vlln/skit)（推荐）

```bash
skit install ./skills/pdf-figures
```

### 手动

| Agent | 命令 |
|---|---|
| **Claude Code** | `cp -r skills/pdf-figures .claude/skills/` |
| **Codex** | `cp -r skills/pdf-figures ~/.codex/skills/` |
| **Kimi** | `cp -r skills/pdf-figures ~/.kimi/skills/` |
| **OpenCode** | `cp -r skills/pdf-figures ~/.opencode/skills/` |

## Skills

| Skill | Description |
|---|---|
| [pdf-figures](skills/pdf-figures/SKILL.md) | 纯像素取图：渲染、定位、裁剪、导出，不做版面解析。 |

## 用法

```bash
export _S="/path/to/skills/pdf-figures"

python3 $_S/scripts/pdfx.py probe paper.pdf                    # 页数、背景、是否扫描件
python3 $_S/scripts/pdfx.py sheet paper.pdf                    # 全篇缩略图
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --ruler --out p3.png
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --box 120,150,1000,900 --scale 2 --ruler --out z.png
python3 $_S/scripts/pdfx.py norm  paper.pdf --boxes "0,0,300,120" --origin 120,150
python3 $_S/scripts/pdfx.py export paper.pdf out/ spec.json --dpi 300 --tight
python3 $_S/scripts/pdfx.py selftest
```

输入可以是 PDF，也可以直接是页面图片。`view` 的刻度以**本视图左上角**为原点、单位是整页像素；
换算回归一化坐标用 `norm`（`view` 会回传 `origin_px` 与 `scale`）。

## Requirements

- Python 3.10+
- `pypdfium2`、`Pillow`（两个 wheel，无系统二进制）

## 评测

```bash
python3 bench/acceptance.py    # 硬性闸口
python3 bench/selftest.py      # 评分链路合成验证
```

逐 run 的可用性判定见 [bench/pilot/EVIDENCE.md](bench/pilot/EVIDENCE.md)，完整报告见 [REPORT.md](REPORT.md)。

## 它不做什么

不做版面结构化。要整篇 markdown、公式、表格内容，用 MinerU 之类的解析器。

## License

MIT。评测数据来自 OmniDocBench（CC-BY-4.0），不随本仓库分发。
