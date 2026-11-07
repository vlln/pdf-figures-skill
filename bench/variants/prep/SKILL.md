# 取图（预渲染刻度法）

刻度要在看图之前就画好。整批跑一条命令，之后每页只看一次。

```bash
export _S="/Users/vlln/Projects/Skills/pdf-figures-skill/skills/pdf-figures"
python3 $_S/scripts/pdfx.py prep . ./ruled --dpi 150 --step 50
```

流程：
1. 对当前目录整批跑一次 `prep`，得到 `ruled/` 下带像素刻度的页面图（每 50 像素一条线，
   标签是像素坐标）；
2. 逐张看 `ruled/` 里的图，按刻度读出每个图/表的边界；
3. 输出用**归一化坐标**，键用**原始页面文件名**（不是 ruled/ 里的 .png 名）。

只框图/表本体，不含图注、标题、正文、页眉页脚。
