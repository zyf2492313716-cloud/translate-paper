#!/usr/bin/env python3
"""Split a paper PDF into translation units.

Roles:
  prose   - a flowing paragraph (one block) -> translate as a whole, re-wrap into its rect
  cell    - one table cell -> translate in place so the grid survives
  skipped - rotated text, running head/footer, reference list, number-only cells

Run-in bold labels inside a paragraph are wrapped in {{b}}...{{/b}} markers; the
fill step renders them with the bold face.
"""
import argparse
import json
import re

import pymupdf

LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}
HAS_LETTER = re.compile(r"[A-Za-z]{2}")
REF_ENTRY = re.compile(r"^\d{1,3}\s+[A-Z][a-zA-Z]")


def tidy(s):
    for lig, exp in LIG.items():
        s = s.replace(lig + " ", exp).replace(lig, exp)
    s = s.replace("\u00ad", "").replace("’", "'").replace("‘", "'")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" +([,.;:!?)%])", r"\1", s)
    s = re.sub(r"\( +", "(", s)
    return s.strip()


def is_bold(span):
    return bool(span["flags"] & 2**4) or "Bold" in span["font"]


def line_text(line):
    return "".join(s["text"] for s in line["spans"])


def sup(line, modal):
    return line["spans"][0]["size"] < 0.78 * modal


def marked_line(line):
    spans = [s for s in line["spans"] if s["text"].strip()]
    if not spans:
        return ""
    out = ""
    if is_bold(spans[0]) and len(spans[0]["text"].strip()) <= 26:
        out += "{{b}}" + spans[0]["text"].strip() + "{{/b}} "
        spans = spans[1:]
    return out + "".join(s["text"] for s in spans)


def row_clusters(lines):
    rows = []
    for ln in sorted(lines, key=lambda l: l["bbox"][1]):
        y0, y1 = ln["bbox"][1], ln["bbox"][3]
        for row in rows:
            if min(y1, row[-1]["bbox"][3]) - max(y0, row[-1]["bbox"][1]) > 0.5 * (y1 - y0):
                row.append(ln)
                break
        else:
            rows.append([ln])
    return rows


def is_table_like(rows):
    hits = sum(1 for r in rows if len(r) > 1 and sorted(r, key=lambda l: l["bbox"][0])[1]["bbox"][0]
               - sorted(r, key=lambda l: l["bbox"][0])[0]["bbox"][2] > 3)
    return hits >= 1 and hits >= 0.35 * len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--output", default="units.json")
    ap.add_argument("--keep-refs", action="store_true", help="also translate the reference list")
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    units, skipped = [], []
    in_refs = False
    for pno, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            lines = [l for l in block["lines"]
                     if l["spans"] and line_text(l).strip()
                     and tuple(round(v, 1) for v in l["dir"]) == (1.0, 0.0)]
            if not lines:
                continue
            full = tidy(" ".join(line_text(l) for l in lines))
            top = min(l["bbox"][1] for l in lines)
            sizes = [(round(s["size"], 1), max(len(s["text"].strip()), 1))
                     for l in lines for s in l["spans"]]
            size = max({z for z, _ in sizes}, key=lambda z: sum(w for s, w in sizes if s == z))
            if full.startswith("References"):
                in_refs = True
            if in_refs and not args.keep_refs:
                skipped.append({"page": pno, "why": "references", "text": full[:70]})
                continue
            if size <= 7.5 and (top < 60 or top > 760):
                skipped.append({"page": pno, "why": "headfoot", "text": full[:70]})
                continue
            if in_refs and REF_ENTRY.match(full):
                skipped.append({"page": pno, "why": "references", "text": full[:70]})
                continue

            rows = row_clusters([l for l in lines if not sup(l, size)])
            if is_table_like(rows):
                for ln in lines:
                    for s in ln["spans"]:
                        txt = tidy(s["text"])
                        if not txt or not HAS_LETTER.search(txt):
                            continue
                        units.append({"id": len(units), "page": pno, "role": "cell",
                                      "rect": [round(v, 2) for v in s["bbox"]],
                                      "redact": [[round(v, 2) for v in s["bbox"]]],
                                      "size": round(s["size"], 2), "bold": is_bold(s),
                                      "color": s["color"], "text": txt})
                continue

            mains = sorted([l for l in lines if not sup(l, size)],
                           key=lambda l: (round(l["bbox"][1], 1), l["bbox"][0]))
            chunks = [marked_line(l) for l in mains]
            for ln in lines:  # superscript citation markers glue onto their line
                if not sup(ln, size):
                    continue
                best, best_ov = None, 0.0
                for i, m in enumerate(mains):
                    ov = min(ln["bbox"][3], m["bbox"][3]) - max(ln["bbox"][1], m["bbox"][1])
                    if ov > best_ov:
                        best, best_ov = i, ov
                if best is not None:
                    chunks[best] += marked_line(ln).replace(" ", "")
            body = tidy(" ".join(c for c in chunks if c))
            if not body:
                continue
            units.append({"id": len(units), "page": pno, "role": "prose",
                          "rect": [round(v, 2) for v in block["bbox"]],
                          "redact": [[round(v, 2) for v in l["bbox"]] for l in lines],
                          "size": size, "color": lines[0]["spans"][0]["color"],
                          "text": body})

    json.dump({"units": units, "skipped": skipped}, open(args.output, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(re.sub(r"\.json$", ".txt", args.output), "w", encoding="utf-8") as fh:
        for u in units:
            fh.write(f"[{u['id']}] {u['text']}\n")
    pr = sum(1 for u in units if u["role"] == "prose")
    print(f"{len(units)} units ({pr} prose, {len(units) - pr} cells), "
          f"{sum(len(u['text']) for u in units)} chars, {len(skipped)} blocks skipped -> {args.output}")
    print(f"translate in batches, then merge with merge_translations.py")


if __name__ == "__main__":
    main()
