#!/usr/bin/env python3
"""Typeset translations back into the PDF at their original positions.

Only text carrying a translation is erased (line-accurate redaction that keeps
table rules, background art and images); the new text is re-wrapped into the
same rectangle, shrinking the font until it fits. Closing punctuation is never
left to lead a line (kinsoku).

Usage: fill_units.py in.pdf translations.json units.json out.pdf [--lang zh]
"""
import argparse
import glob
import json
import os
import re
import sys

import pymupdf

NO_START = set("。，、；：！？）】》”’％…—·")
SHRINKS = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5)
SERIF_FAM = ["SourceHanSerifSC", "NotoSerifCJKsc", "NotoSerif", "TimesNewRoman"]
SANS_FAM = ["SourceHanSansSC", "NotoSansCJKsc", "PingFang"]
DIRS = [os.path.expanduser("~/Library/Fonts"), "/Library/Fonts",
        "/System/Library/Fonts/Supplemental", "/System/Library/Fonts"]
TTC = [("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC "),
       ("/System/Library/Fonts/Supplemental/STHeiti Light.ttc", "Heiti SC ")]
CACHE = os.path.expanduser("~/.cache/translate-paper")


def _glob_families(families):
    for family in families:
        for d in DIRS:
            reg = glob.glob(os.path.join(d, f"{family}-Regular.o*")) + \
                glob.glob(os.path.join(d, f"{family}-*Regular.o*")) + \
                glob.glob(os.path.join(d, f"{family}-*Roman*Regular*"))
            if reg:
                bold = glob.glob(os.path.join(d, f"{family}-Bold.o*")) + \
                    glob.glob(os.path.join(d, f"{family}-*Bold.o*"))
                return reg[0], (bold[0] if bold else reg[0])
    return None


def _face_from_ttc(path, face_name):
    """Extract one face out of a .ttc so PyMuPDF can embed it."""
    if not os.path.exists(path):
        return None
    os.makedirs(CACHE, exist_ok=True)
    try:
        from fontTools.ttLib import TTCollection
        names = [f["name"].getDebugName(4) for f in TTCollection(path).fonts]
        if face_name not in names:
            return None
        out = os.path.join(CACHE, face_name.replace(" ", "") + ".otf")
        if not os.path.exists(out):
            TTCollection(path).fonts[names.index(face_name)].save(out)
        return out
    except Exception:
        return None


def find_font_files():
    """Serif first (journal body is serif), then a serif face from a .ttc, then sans."""
    hit = _glob_families(SERIF_FAM)
    if hit:
        return hit
    for path, prefix in TTC:
        reg = _face_from_ttc(path, prefix + "Regular")
        if reg:
            return reg, _face_from_ttc(path, prefix + "Bold") or reg
    hit = _glob_families(SANS_FAM)
    if hit:
        return hit
    return None, None


def load_fonts():
    reg, bold = find_font_files()
    if reg:
        return {"regular": pymupdf.Font(fontfile=reg), "bold": pymupdf.Font(fontfile=bold),
                "names": ("ZhR", "ZhB"), "files": (reg, bold)}
    return {"regular": pymupdf.Font("china-ss"), "bold": pymupdf.Font("china-ss"),
            "names": ("china-ss", "china-ss"), "files": (None, None)}


F = load_fonts()
FONTS = {False: F["regular"], True: F["bold"]}
MARK = re.compile(r"\{\{/?b\}\}")


def _cjk(ch):
    return bool(ch) and ord(ch) >= 0x2E80


def to_rgb(c):
    return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)


def segments(text):
    out = []
    for i, part in enumerate(re.split(r"\{\{b\}\}(.*?)\{\{/b\}\}", text, flags=re.S)):
        if part:
            out.append((part, i % 2 == 1))
    return out


def tokenize(s):
    """CJK breaks anywhere; latin runs break only at spaces."""
    toks, cur = [], ""
    for ch in s:
        if ch == " ":
            if cur:
                toks.append(cur)
                cur = ""
            toks.append(" ")
        elif ch == "\x01":
            if cur:
                toks.append(cur)
                cur = ""
            toks.append("{{br}}")
        elif ord(ch) < 0x2E80:
            cur += ch
        else:
            if cur:
                toks.append(cur)
                cur = ""
            toks.append(ch)
    if cur:
        toks.append(cur)
    merged = []
    for t in toks:
        if t == " " and merged:
            merged[-1] += t
        else:
            merged.append(t)
    return merged


def flow(stream, size, width):
    lines, cur, w = [], [], 0.0
    for tok, bold in stream:
        if tok == "{{br}}":
            if cur:
                lines.append(cur)
            cur, w = [], 0.0
            continue
        tw = FONTS[bold].text_length(tok, size)
        if cur and w + tw > width:
            hang = len(tok) == 1 and tok in NO_START and w <= width + 0.6
            if not hang:
                lines.append(cur)
                cur, w = [], 0.0
        cur.append((tok, bold))
        w += tw
    if cur:
        lines.append(cur)
    return lines


def runs(line):
    out = []
    for tok, bold in line:
        if out and out[-1][1] == bold:
            out[-1][0] += tok
        else:
            out.append([tok, bold])
    return out


_EXT = {}


def _extents(name, fontfile, size, rot):
    """How far insert_text(rotate=rot) grows either side of its anchor, measured
    with the real face rather than guessed from font metrics."""
    key = (name, fontfile, round(size, 2), rot)
    if key not in _EXT:
        d = pymupdf.open()
        pg = d.new_page(width=200, height=200)
        kw = {"fontsize": size, "rotate": rot}
        if fontfile:
            kw["fontfile"] = fontfile
        pg.insert_text((100, 100), "中H", fontname=name, **kw)
        b = pg.get_text("dict")["blocks"][0]["lines"][0]["bbox"]
        _EXT[key] = (100 - b[0], b[2] - 100)
        d.close()
    return _EXT[key]


def place_rot(page, unit, text, size, rect):
    """A vertical figure label: one run along the original advance, centred on it."""
    text = MARK.sub("", text).strip()
    if not text:
        return None
    bold = bool(unit.get("bold"))
    name, file = F["names"][1 if bold else 0], F["files"][1 if bold else 0]
    adv = FONTS[bold].text_length(text, size)
    if adv > rect.height + 1.5:  # the original column is the only room there is
        return None
    rot = unit["rot"]
    dl, dr = _extents(name, file, size, rot)
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    ax = cx + (dl - dr) / 2
    ay = cy + adv / 2 if rot == 90 else cy - adv / 2
    page.insert_text((ax, ay), text, fontname=name, fontfile=file, fontsize=size,
                     color=to_rgb(unit["color"]), rotate=rot)
    near, far = (ay - adv, ay) if rot == 90 else (ay, ay + adv)
    return {"size": round(size, 2), "lines": 1,
            "drawn": [round(ax - dl, 1), round(near, 1), round(ax + dr, 1), round(far, 1)]}


def place(page, unit, text, size, leading):
    """Draw one unit; return metrics when it fits, None when it does not."""
    body_col = to_rgb(unit["color"])
    label_col = to_rgb(unit.get("label_color", unit["color"]))
    rect = pymupdf.Rect(unit["rect"])
    if unit.get("rot"):
        return place_rot(page, unit, text, size, rect)
    pad = 0.6
    if unit["role"] == "cell":
        rect.x1 += 4  # narrow columns: borrow a little of the inter-column gap
    stream, broke = [], False
    for seg, bold in segments(text):
        if stream and seg and not stream[-1][0].endswith(" "):
            prev_bold, prev_char = stream[-1][1], stream[-1][0][-1]
            if (bold and _cjk(prev_char)) or (prev_bold and _cjk(prev_char) and _cjk(seg[0])):
                seg = " " + seg      # keep a run-in label from gluing onto its body
        stream += [(tok, bold) for tok in tokenize(seg)]
        if bold and unit.get("br_after_label") and not broke:
            stream.append(("{{br}}", False))
            broke = True
    lines = flow(stream, size, rect.width - 2 * pad)
    if (len(lines) - 1) * leading + size * 1.05 > rect.height + 1.5:
        return None
    forced = bool(unit.get("bold"))  # whole block set in a bold face (summary box, bold cells)
    y = rect.y0 + size * 0.88
    widest = 0.0
    for line in lines:
        x = rect.x0 + pad
        for txt, seg_bold in runs(line):
            # the FACE follows weight (marker or whole-block), the INK follows the
            # label marker alone: a forced-bold body must not turn into label red
            use_bold = seg_bold or forced
            page.insert_text((x, y), txt, fontname=F["names"][1] if use_bold else F["names"][0],
                             fontsize=size, color=label_col if seg_bold else body_col)
            x += FONTS[use_bold].text_length(txt, size)
        widest = max(widest, x)
        y += leading
    return {"size": round(size, 2), "lines": len(lines),
            "drawn": [round(rect.x0, 1), round(rect.y0, 1), round(widest, 1), round(y - leading, 1)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("translations")
    ap.add_argument("units")
    ap.add_argument("out")
    ap.add_argument("--manifest", default="fill_manifest.json",
                    help="per-unit placement metrics for audit_layout.py")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    units = {u["id"]: u for u in json.load(open(args.units))["units"]}
    trans = {int(k): v for k, v in json.load(open(args.translations)).items()}
    by_page = {}
    for uid, text in trans.items():
        by_page.setdefault(units[uid]["page"], []).append((uid, text))

    tight, placed = [], {}
    for pno, items in sorted(by_page.items()):
        page = doc[pno]
        for uid, _ in items:
            for r in units[uid]["redact"]:
                page.add_redact_annot(pymupdf.Rect(r) + (0, -0.45, 0, 0.45))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if F["files"][0]:
            page.insert_font(fontname="ZhR", fontfile=F["files"][0])
            page.insert_font(fontname="ZhB", fontfile=F["files"][1])
        for uid, text in items:
            unit = units[uid]
            for s in SHRINKS:
                size = round(unit["size"] * s, 2)
                got = place(page, unit, text, size, size * 1.2)
                if got:
                    got.update(page=pno, role=unit["role"], factor=s, base=unit["size"],
                               rect=unit["rect"], rot=unit.get("rot", 0),
                               chars=len(MARK.sub("", text)))
                    placed[uid] = got
                    if s < 0.8:
                        tight.append((uid, s))
                    break
            else:
                got = place(page, unit, text, round(unit["size"] * 0.5, 2), unit["size"] * 0.6)
                if got:
                    got.update(page=pno, role=unit["role"], factor=0.0, base=unit["size"],
                               rect=unit["rect"], rot=unit.get("rot", 0),
                               chars=len(MARK.sub("", text)))
                    placed[uid] = got
                tight.append((uid, "forced"))
    # a unit that was erased and then not redrawn is a hole in the page, and no
    # downstream gate can see it: the source letters are gone, so LEFTOVER stays quiet
    dropped = sorted(uid for uid in trans if uid not in placed)
    doc.subset_fonts(fallback=True)  # CFF faces need fontTools as backup
    doc.save(args.out, garbage=4, deflate=True)
    placed["_fonts"] = {"names": list(F["names"]),
                        "faces": sorted({f.name for f in FONTS.values()})}
    json.dump(placed, open(args.manifest, "w"), ensure_ascii=False)
    print(f"wrote {args.out} ({os.path.getsize(args.out)/1e6:.2f} MB); "
          f"{len(tight)} units below 0.8x: {tight[:10]}")
    if dropped:
        for uid in dropped:
            u = units[uid]
            print(f"  DROPPED [{uid}] p{u['page'] + 1} {u['role']}: nothing from "
                  f"{u['size']}pt down to 0.5x fits {trans[uid][:26]!r} into "
                  f"{round(u['rect'][2] - u['rect'][0])}x{round(u['rect'][3] - u['rect'][1])}pt")
        print("GATE: FAIL (redacted but never redrawn)")
        sys.exit(1)


if __name__ == "__main__":
    main()
