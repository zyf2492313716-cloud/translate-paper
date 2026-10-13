#!/usr/bin/env python3
"""Compose a bilingual PDF from a source paper and its in-place translation.

  --mode page     whole source page beside whole translated page (2x width)
                  the form zotero-pdf2zh's *.dual.pdf uses
  --mode column   crop each column and pair it with its own translation
                  better for two-column journals: text stays at 100% and the eye
                  does not travel across a 1200 pt sheet

Usage: make_dual.py source.pdf translated.pdf out.pdf [--mode page|column] [--gap 12]
"""
import argparse
import sys

import pymupdf


def column_bands(page):
    """Column gutter from the widest white band near the page centre.

    Only column-width blocks vote: a full-width element (the Lancet summary box, a
    table, a figure) bridges the gutter and would hide it.
    """
    rects = [b["bbox"] for b in page.get_text("dict")["blocks"]
             if b.get("type") == 0 and b["lines"]]
    if not rects:
        return [page.rect]
    top = min(r[1] for r in rects)
    bottom = max(r[3] for r in rects)
    narrow = sorted((r for r in rects if r[2] - r[0] < 0.5 * page.rect.width), key=lambda r: r[0])
    merged = []
    for r in narrow:
        if merged and r[0] <= merged[-1][1] + 2:
            merged[-1][1] = max(merged[-1][1], r[2])
        else:
            merged.append([r[0], r[2]])
    mid = page.rect.width / 2
    left_edge = min(r[0] for r in rects)
    right_edge = max(r[2] for r in rects)
    span = right_edge - left_edge
    # a real gutter leaves two usable columns; a running-head block does not
    gaps = [(abs((a[1] + b[0]) / 2 - mid), b[0] - a[1], a[1], b[0])
            for a, b in zip(merged, merged[1:])
            if b[0] - a[1] > 4 and min(b[0] - left_edge, right_edge - a[1]) > 0.28 * span]
    if not gaps:
        return [pymupdf.Rect(left_edge - 2, top - 2, right_edge + 2, bottom + 2)]
    lo, hi = min(gaps)[2], min(gaps)[3]
    split = (lo + hi) / 2
    # a title or summary box that straddles the gutter would be cut mid-word: keep the page whole.
    # Running head/footer furniture (full width, one short line) crosses every split; ignore it.
    body = [r for r in rects if not (r[2] - r[0] > 0.9 * span and r[3] - r[1] < 12)]
    if any(r[0] + 2 < split < r[2] - 2 for r in body):
        return [pymupdf.Rect(left_edge - 2, top - 2, right_edge + 2, bottom + 2)]
    return [pymupdf.Rect(left_edge - 2, top - 2, split, bottom + 2),
            pymupdf.Rect(split, top - 2, right_edge + 2, bottom + 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("translated")
    ap.add_argument("out")
    ap.add_argument("--gap", type=float, default=12.0)
    ap.add_argument("--mode", choices=["page", "column"], default="page")
    ap.add_argument("--right-left", action="store_true", help="translation first (left)")
    args = ap.parse_args()

    src, trn = pymupdf.open(args.source), pymupdf.open(args.translated)
    n = min(src.page_count, trn.page_count)
    if src.page_count != trn.page_count:
        print(f"warning: page counts differ ({src.page_count} vs {trn.page_count}); using {n}")
    if abs(src[0].rect.width - trn[0].rect.width) > 1:
        print("warning: page widths differ; bands come from the source and may clip the translation")

    out = pymupdf.open()
    for i in range(n):
        for band in (column_bands(src[i]) if args.mode == "column" else [src[i].rect]):
            w, h = band.width * 2 + args.gap, band.height
            page = out.new_page(width=w, height=h)
            xs = (band.width + args.gap, 0) if args.right_left else (0, band.width + args.gap)
            page.show_pdf_page(pymupdf.Rect(xs[0], 0, xs[0] + band.width, h), src, i, clip=band)
            page.show_pdf_page(pymupdf.Rect(xs[1], 0, xs[1] + band.width, h), trn, i, clip=band)
            mx = min(xs) + band.width + args.gap / 2
            page.draw_line(pymupdf.Point(mx, 0), pymupdf.Point(mx, h),
                           color=(0.72, 0.72, 0.72), width=0.5)
    out.save(args.out, garbage=3, deflate=True)
    print(f"wrote {args.out}: {out.page_count} pages ({args.mode}), "
          f"{out[0].rect.width:.0f}x{out[0].rect.height:.0f} pt")
    sys.exit(0)


if __name__ == "__main__":
    main()
