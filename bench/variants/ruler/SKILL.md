# 取图（尺子法）

在图上叠刻度，按刻度读数；放大再看，刻度数字不变。

```bash
export _S="/Users/vlln/Projects/Skills/pdf-figures-skill/skills/pdf-figures"
python3 $_S/scripts/pdfx.py ruler page.jpg 1 --dpi 200 --step 50 --out page_ruler.png
python3 $_S/scripts/pdfx.py zoom  page.jpg 1 --box 120,150,1000,900 --scale 1.6 --step 25 --out z.png
```

流程：
1. 先看 `ruler` 出的整页图，粗定每个图/表的像素框；
2. 对拿不准的边界，用 `zoom` 放大那块再看，读同一套像素刻度；
3. 最终框用**像素**给，脚本会换算成归一化坐标（`zoom` 返回里带 `norm`）。

只框图/表本体，不含图注、标题、正文、页眉页脚。
