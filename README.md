# pdf-figures

> **能看图的模型，能不能把 PDF 里的图和表取出来？能取到什么程度？**

这个仓库是回答这两个问题的实验，以及由此沉淀下来的一个 Agent Skill。

## 结论

| 问题 | 答案 |
|---|---|
| 能不能取 | 能。只给渲染后的像素，不给文本层、不给矢量结构。 |
| 有多准 | deepseek-flash：8 页 22 个目标，**合并口径 22/22**；严格口径 17/22，差的 4 个是"多面板该合成一个框"的标注冲突。 |
| 有多快 | 生产版技能 **79 秒/页**（3 次，76–81），裸模型 99 秒/页（3 次，86–109）。 |
| 换模型行不行 | kimi-k2.6 **不行**：慢 6.7 倍，一半页面 900 秒做不完；glm-5.3 只收文字，图都进不去。 |

## 实验

8 页分层样本（学术 / 书籍 / 杂志 × 图 / 表 / 混排 / 多目标），每页独立沙箱、禁联网、每次全新目录。

| 组 | 做法 | F1（严格 / 合并） | 秒/页 | 一句话 |
|---|---|---|---|---|
| e1 | 裸模型 | 0.850 / **1.000** | 99.4 | 3 次，86–109 |
| e2b | 尺子技能 | 0.850 / **1.000** | 90.4 | 单次 |
| e3b | 像素技能 | 0.850 / **1.000** | 93.3 | 单次，干净重跑 |
| e4 | 尺子 + 复审 | 0.850 / **1.000** | 95.7 | 框与 e2b 逐像素重合，白花时间 |
| e5 | 四页拼一张 | 作废 | 67.3 | 分辨率除以 4，模型拿到就拆回去 |
| e5b–e5d | 打包开关迭代 | 0.850 / **1.000** | 92.8–132.2 | 措辞只改用量，不改分数 |
| **p1** | **生产版技能** | 0.850 / **1.000** | **79.0** | 3 次，76–81，最快也最稳 |
| m2 | kimi-k2.6（无技能） | **0.421** | 666.8 | 4/8 超时；跑完的四页 mIoU 0.96 |

## 三点解读

**质量饱和，成本不饱和。** 所有 deepseek 组都是 22/22，这批题分不出方法高低；能分开的只有耗时。

**技能省的是长尾，不是均匀加速。** 8 页里只有 5 页更快：裸模型在个别页反复探边（129 秒），装技能后收敛到 44 秒；本来一遍过的简单页反而多花几秒。

**瓶颈是推理开销，不是眼睛。** kimi 跑完的页面定位精度 0.96 mIoU，但一页要 11 分钟——看得准，算得慢。

## 安装

```bash
pip install pypdfium2 pillow
skit install ./skills/pdf-figures
```

两个 wheel，下载 8 MB / 安装 21 MB，无系统二进制。

## 用法

```bash
export _S=$(skit path pdf-figures)

python3 $_S/scripts/pdfx.py probe paper.pdf                    # 页数、背景、是否扫描件
python3 $_S/scripts/pdfx.py sheet paper.pdf                    # 全篇缩略图
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --ruler --out p3.png
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --box 120,150,1000,900 --scale 2 --ruler --out z.png
python3 $_S/scripts/pdfx.py norm  paper.pdf --boxes "0,0,300,120" --origin 120,150
python3 $_S/scripts/pdfx.py export paper.pdf out/ spec.json --dpi 300 --tight
python3 $_S/scripts/pdfx.py selftest
```

输入可以是 PDF，也可以直接是页面图片。

## 坐标

| 名字 | 范围 | 用在哪 |
|---|---|---|
| 整页归一化 | 0..1，左上原点 | `norm` 输出、`export` 输入 |
| 整页像素 | 200 dpi 渲染下的像素 | `view` 的 `--box/--boxes/--pack` |
| 视图局部像素 | 本视图左上角为 0,0 | 刻度标签、`norm --boxes` |

刻度数字随裁剪框变，换框必须重新换算——`view` 回传 `origin_px` 与 `scale`，照抄给 `norm` 即可。

## 它不做什么

- 不解析文本层、不解析矢量图元（扫描件与矢量页因此同一条路径）
- 不做版面结构化；要整篇 markdown、公式、表格内容，请用 MinerU 之类
- 不保证小图、稀疏折线图、密集表格的召回——已知边界

## 限制

- 主结果来自 **8 页**，且质量已饱和；结论的价值在成本与失败模式，不在"谁更准"
- 除 `p1` 与 `e1` 外，各组只有单次运行
- 只测了两个模型；GLM 家族在本机没有可用的视觉通路

## 目录

```
skills/pdf-figures/      交付物：SKILL.md + scripts/pdfx.py + references/
bench/                   评分器、自检、pilot 运行器
bench/pilot/EVIDENCE.md  每个 run 的可用性判定（哪些作废、为什么）
REPORT.md                完整报告：设置、结果、结论强度
bench/archive/           2026-11-04 旧报告（结论已被推翻）
```

## 自检

```bash
python3 $_S/scripts/pdfx.py selftest
python3 bench/selftest.py
python3 bench/acceptance.py
```

## 许可

MIT。评测数据来自 OmniDocBench（CC-BY-4.0），不随本仓库分发。
