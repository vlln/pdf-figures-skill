# 取图（结构法）

用脚本先把页面拆成候选块，你只做判断。

```bash
export _S="/Users/vlln/Projects/Skills/pdf-figures-skill/skills/pdf-figures"
python3 $_S/scripts/pdfx.py scan page.jpg 1                 # 行带 + 候选块（带 box/area/ink）
python3 $_S/scripts/pdfx.py scan page.jpg 1 --montage m.png # 同时生成编号拼图
python3 $_S/scripts/pdfx.py coverage page.jpg 1 --prior p.json  # 还没被覆盖的墨迹区
```

流程：
1. `scan` 拿候选块；看整页图与拼图，判断哪些块是图/表本体；
2. 直接采用 `scan` 返回的 `box` 作为答案（不要自己写坐标）；
3. 用 `coverage` 检查是否有大块未覆盖区域，若有再回头看一眼。

只框图/表本体，不含图注、标题、正文、页眉页脚。
输出归一化坐标 0..1。
