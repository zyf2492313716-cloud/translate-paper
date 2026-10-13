#!/usr/bin/env python3
"""Two gates on the finished PDF.

1. replaced units must carry no leftover source letters (only their own translation)
2. untouched units (kept English, numbers) must survive redaction intact
"""
import json
import re
import sys

import pymupdf

MARK = re.compile(r"\{\{/?b(?:r)?\}\}")


def runs(text):
    return sorted(re.findall(r"[A-Za-z]+", MARK.sub("", text)))


def chars_in(page, rect):
    """Letters whose horizontal centre falls inside rect (avoids neighbour cells)."""
    out = ""
    for blk in page.get_text("rawdict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk["lines"]:
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    b = pymupdf.Rect(ch["bbox"])
                    if rect.x0 - 0.5 <= b.x0 <= rect.x1 - 0.2 and abs(b.y1 - rect.y1) < 3.5:
                        out += ch["c"]
    return out


def main(pdf, units_file, trans_file):
    doc = pymupdf.open(pdf)
    units = {u["id"]: u for u in json.load(open(units_file))["units"]}
    trans = {int(k): v for k, v in json.load(open(trans_file)).items()}
    fails = 0

    for uid in sorted(trans):
        u = units[uid]
        rect = pymupdf.Rect(u["rect"])
        got = chars_in(doc[u["page"]], rect)
        extra = sorted(set(runs(got)) - set(runs(trans[uid])))
        if extra:
            fails += 1
            print(f"  LEFTOVER [{uid}] p{u['page']+1} {extra} src={u['text'][:26]!r} zh={trans[uid][:26]!r}")
        over = max((pymupdf.Rect(ch["bbox"]).x1 for blk in doc[u["page"]].get_text("rawdict")["blocks"]
                    if blk.get("type") == 0 for ln in blk["lines"] for sp in ln["spans"]
                    for ch in sp["chars"]
                    if abs(pymupdf.Rect(ch["bbox"]).y1 - rect.y1) < 3.5
                    and rect.x0 - 1 <= pymupdf.Rect(ch["bbox"]).x0 <= rect.x1 + 0.2), default=0)
        if over > rect.x1 + (5 if u['role'] == 'cell' else 2.5):
            fails += 1
            print(f"  OVERFLOW [{uid}] p{u['page']+1} text ends {over:.1f} > rect {rect.x1:.1f}")

    for uid, u in sorted(units.items()):
        if uid in trans or not re.search(r"[A-Za-z]", u["text"]):
            continue
        rect = pymupdf.Rect(u["rect"])
        got = "".join(ch for ch in chars_in(doc[u["page"]], rect + (-1.5, 0, 1.5, 0)))
        if runs(got) != runs(u["text"]):
            fails += 1
            print(f"  CLIPPED [{uid}] p{u['page']+1} kept={u['text'][:20]!r} found={runs(u['text'])} vs {runs(got)}")

    print("GATE:", "FAIL" if fails else "PASS", f"({fails} problems)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
