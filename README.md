# pdf-figures-skill

把 PDF 当**纯像素**来取图的 Agent Skill：不解析文本层、不解析矢量图元，因此矢量 PDF、扫描件、
图片型 PDF 走同一条路径。定位靠模型的眼睛，精度靠脚本的像素算子。

## 安装

```bash
pip install pypdfium2 pillow
skit install ./skills/pdf-figures
```

只依赖两个 wheel（约 8 MB 下载、21 MB 安装），无系统二进制。

## 快速使用

```bash
export _S=$(skit path pdf-figures)     # 或直接写绝对路径

python3 $_S/scripts/pdfx.py probe paper.pdf              # 页数、背景、是否扫描件
python3 $_S/scripts/pdfx.py sheet paper.pdf              # 全篇缩略图，先分诊
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --ruler --out p3.png
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --box 120,150,1000,900 --scale 2 --ruler --out z.png
python3 $_S/scripts/pdfx.py view  paper.pdf --page 3 --boxes "120,340,980,760" --ruler --out chk.png
python3 $_S/scripts/pdfx.py norm  paper.pdf --boxes "0,0,300,120" --origin 120,150
python3 $_S/scripts/pdfx.py strip paper.pdf 3 0.55 0.72  # 图注边界按行切
python3 $_S/scripts/pdfx.py export paper.pdf out/ spec.json --dpi 300 --tight
python3 $_S/scripts/pdfx.py selftest                     # 自检
```

输入可以是 PDF，也可以直接是页面图片（内部会包成单页 PDF）。

## 它能做什么 / 不能做什么

- 能：把 PDF 渲染成像素、按模型判断定位图表、按坐标精确裁剪并按 DPI 重渲染、
  导出带 manifest 的图片；对扫描件同样有效（退化页与干净页的收缩框 IoU 0.985）。
- 能：用 `leftover` 把「已有框之外还剩什么」摆成编号选择题，找回约一半的漏检。
- 精度：留出集 80 页上 F1 **0.882**，裸视觉模型 0.824，MinerU 0.762；
  dev 干净页上与裸模型打平（0.980 vs 0.980）。**收益集中在难页**（+0.146），易页几乎为零。
- 这个增益来自指令（两遍法 + 漏项清单），不是脚本——同一切片上「只给清单不装技能」同分。
  脚本贡献的是确定性裁剪、导出与召回安全网，别把它当成算法优势。
- 不能：替代版面解析。要结构化 markdown、公式、表格内容时，先跑 MinerU 之类的解析器。

评测细节与失败案例见 [REPORT.md](REPORT.md) 与 `skills/pdf-figures/references/`。

## 评测

`bench/` 下是可复现的评测台：固定数据集清单（含 sha256）、薄适配器、独立评分器。

```bash
python3 bench/acceptance.py     # 硬性闸口
python3 bench/selftest.py       # 评分链路合成验证
python3 bench/scan_check.py ... # 扫描退化集的脚本层回归
```

结果见 [REPORT.md](REPORT.md)。

## 许可

MIT。评测数据来自 OmniDocBench（CC-BY-4.0），不随本仓库分发。
