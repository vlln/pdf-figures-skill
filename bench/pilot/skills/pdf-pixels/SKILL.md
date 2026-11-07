---
name: pdf-pixels
description: >
  Use when you need to render PDF pages, crop regions, or measure where content
  actually starts and ends on a page — for example to find or tighten the
  boundaries of figures and tables. Works on pixels only, so scanned PDFs and
  page images behave the same as vector PDFs.
---

# PDF Pixels

Small pixel tools so you do not have to write rendering or projection code yourself.

```bash
export _S="/absolute/path/to/this/skill"
python3 $_S/scripts/pixels.py render  <input> --out page.png
python3 $_S/scripts/pixels.py crop    <input> --box 0.08,0.20,0.92,0.55 --out c.png [--tight]
python3 $_S/scripts/pixels.py bbox    <input> --box 0.08,0.20,0.92,0.55
python3 $_S/scripts/pixels.py profile <input> --box 0.08,0.20,0.92,0.55 --axis row [--out bands.png]
```

`<input>` is a PDF (`--page N`, default 1) or an image file.

## Coordinates

Every box, in and out, is normalized 0..1 relative to the whole page, top-left origin.
No pixel conversion is needed between subcommands.

## What each tool tells you

| Tool | Returns |
|---|---|
| `render` | the page as an image |
| `crop` | the region as an image, its `ink_box`, and `ink_touches_edge` per side |
| `bbox` | the tight bounding box of non-background pixels inside a region |
| `profile` | ink bands along rows or columns inside a region, separated by blank gaps; `--out` numbers them on an image |

`ink_touches_edge: true` on a side means content continues past your box on that side.
`profile` bands help separate a figure from the caption or text beside it: pick the bands
that belong to the target and use their `from`/`to` as edges.

## Gotchas

- Ink means "anything darker than the background": captions, page numbers, and
  neighbouring text count as ink. `bbox` only tightens; it cannot decide what belongs
  to a figure.
- Dark-background pages are inverted automatically before thresholding.
