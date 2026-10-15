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


def vertical_lines(page, rect, tol):
    """Vertical text runs overlapping one rotated unit's box.

    A y-axis label sits in a narrow x strip that the page's horizontal body text
    crosses too, so a column window alone grabs unrelated lines.  Direction is what
    separates them.
    """
    band = pymupdf.Rect(rect) + (-2, -tol, 2, tol)
    out = []
    for blk in page.get_text("rawdict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk["lines"]:
            if abs(ln["dir"][0]) > 0.2:
                continue
            b = pymupdf.Rect(ln["bbox"])
            if band.intersects(b) and rect.x0 - 2 <= (b.x0 + b.x1) / 2 <= rect.x1 + 2:
                out.append((b, ln))
    return out


def chars_in(page, rect, rot=0):
    """Letters belonging to one unit.

    Horizontal text is selected by its left edge inside the column and a shared
    baseline.  A rotated run advances along y instead, so its characters come from the
    vertical lines overlapping the box: the column is only ~6 pt wide and the inserted
    text a little wider, so an edge test would also catch the neighbouring label.
    """
    out = ""
    if rot:
        for _, ln in vertical_lines(page, rect, 2.5):
            for sp in ln["spans"]:
                out += "".join(ch["c"] for ch in sp["chars"])
        return out
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


def main(pdf, units_file, trans_file, manifest=None):
    doc = pymupdf.open(pdf)
    units = {u["id"]: u for u in json.load(open(units_file))["units"]}
    trans = {int(k): v for k, v in json.load(open(trans_file)).items()}
    man = {int(k): v for k, v in json.load(open(manifest)).items() if k.isdigit()} if manifest else {}
    fails = 0

    for uid in sorted(trans):
        u = units[uid]
        # the box actually typeset into: a label that borrowed whitespace is not overflowing,
        # and reading its surviving chars from the narrower ink box would cut a word in half
        rect = pymupdf.Rect(man.get(uid, {}).get("grown") or u["rect"])
        rot = u.get("rot", 0)
        got = chars_in(doc[u["page"]], rect, rot)
        kept = runs(trans[uid])
        # a tight rect, or a URL the typesetter had to break mid-word, reads back as a fragment of
        # a word the translation kept on purpose: "database" -> "too"/"e" is an edge artifact, not
        # a leftover. A word that is not a piece of some longer kept word still fails.
        extra = sorted(r for r in set(runs(got)) - set(kept)
                       if not any(w != r and (w.startswith(r) or w.endswith(r)) for w in kept))
        if extra:
            fails += 1
            print(f"  LEFTOVER [{uid}] p{u['page']+1} {extra} src={u['text'][:26]!r} zh={trans[uid][:26]!r}")
        tol = 5 if u["role"] == "cell" else 2.5
        if rot:  # advance is vertical here: check both ends of the run
            got = [b for b, _ in vertical_lines(doc[u["page"]], rect, tol)]
            if got:
                low, high = max(b.y1 for b in got), min(b.y0 for b in got)
                if low > rect.y1 + tol or high < rect.y0 - tol:
                    fails += 1
                    print(f"  OVERFLOW [{uid}] p{u['page']+1} runs {high:.1f}..{low:.1f} "
                          f"past rect {rect.y0:.1f}..{rect.y1:.1f}")
            continue
        chars = [pymupdf.Rect(ch["bbox"]) for blk in doc[u["page"]].get_text("rawdict")["blocks"]
                 if blk.get("type") == 0 for ln in blk["lines"] for sp in ln["spans"]
                 for ch in sp["chars"]]
        # every line of the block, not just the one at its bottom edge: a multi-line block whose
        # FIRST line crosses the column is the commonest overflow. A line counts as ours only if it
        # sits wholly inside this unit's vertical span and starts on its left edge, else a table
        # row split across two units blames its neighbour's long line on the short one.
        over, glyph = 0.0, max(2.5, float(man.get(uid, {}).get("size") or u["size"]))
        for blk in doc[u["page"]].get_text("rawdict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln in blk["lines"]:
                lb = pymupdf.Rect(ln["bbox"])
                if abs(ln["dir"][0]) < 0.9 or abs(lb.x0 - rect.x0) > 1.5:
                    continue
                if lb.y0 < rect.y0 - 0.5 or lb.y1 > rect.y1 + 0.5:
                    continue
                over = max(over, lb.x1 - rect.x1)
        if over > glyph:
            fails += 1
            print(f"  OVERFLOW [{uid}] p{u['page']+1} a line runs {over:.1f} past rect "
                  f"{rect.x1:.1f} (one glyph is {glyph:.1f})")

    for uid, u in sorted(units.items()):
        if uid in trans or not re.search(r"[A-Za-z]", u["text"]):
            continue
        rect = pymupdf.Rect(u["rect"])
        got = "".join(ch for ch in chars_in(doc[u["page"]], rect + (-1.5, 0, 1.5, 0), u.get("rot", 0)))
        if runs(got) != runs(u["text"]):
            fails += 1
            print(f"  CLIPPED [{uid}] p{u['page']+1} kept={u['text'][:20]!r} found={runs(u['text'])} vs {runs(got)}")

    print("GATE:", "FAIL" if fails else "PASS", f"({fails} problems)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
