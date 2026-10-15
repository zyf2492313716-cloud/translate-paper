#!/usr/bin/env python3
"""Split a paper PDF into translation units.

Roles:
  prose   - a flowing paragraph (one block) -> translate as a whole, re-wrap into its rect
  cell    - one table cell -> translate in place so the grid survives
  axis    - one vertical run inside a figure (a y-axis label) -> redrawn rotated
  skipped - oblique text, running head/footer, reference list, number-only cells

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


def join_spans(spans):
    """Concatenate spans, never letting a superscript reference glue to the number it follows.

    'one in 116.2' is 116 plus reference 2; joined naively it reads as the decimal 116.2, and a
    translator who copies that statistic has just invented a number. The number gate would also
    demand the literal '116.2' survive, which no faithful translation can do.
    """
    big = max((s["size"] for s in spans), default=0)
    base = max((s["origin"][1] for s in spans), default=0)
    out = ""
    for s in spans:
        t = s["text"]
        if out and t.strip() and out[-1] not in " \u2009" \
                and s["size"] < 0.8 * big and base - s["origin"][1] > 0.25 * big:
            out += " "
        out += t
    return out


def line_text(line):
    return join_spans(line["spans"])


def sup(line, modal):
    return line["spans"][0]["size"] < 0.78 * modal


def rot_deg(dirv):
    """insert_text rotation matching a text run's direction vector."""
    dx, dy = dirv
    if abs(dx) < 0.2:
        return 90 if dy < 0 else 270
    return 180 if dx < 0 else 0


def rotated_unit(line, page):
    """One vertical text run (a figure axis label) as its own unit.

    These used to be dropped, which left axis labels in the source language while
    everything around them was translated.
    """
    spans = [s for s in line["spans"] if s["text"].strip()]
    txt = tidy("".join(s["text"] for s in spans))
    if not spans or not HAS_LETTER.search(txt):
        return None
    cs = [(s["color"], max(len(s["text"].strip()), 1)) for s in spans]
    color = max({c for c, _ in cs}, key=lambda c: sum(w for x, w in cs if x == c))
    bbox = [round(v, 2) for v in line["bbox"]]
    return {"page": page, "role": "axis", "rot": rot_deg(line["dir"]), "rect": bbox,
            "redact": [bbox], "size": round(max(s["size"] for s in spans), 2),
            "color": color, "text": txt}


def marked_line(line, mixed, body_color):
    """Wrap a leading bold span as a run-in label.

    Only when it really is one: either the block mixes bold with regular text, or
    the span carries different ink than the body.  Weight alone is not enough -
    the Lancet summary box sets its whole BODY in a bold face too, so a bold-only
    test marks a random body word ("week.") as a label.
    """
    spans = [s for s in line["spans"] if s["text"].strip()]
    if not spans:
        return ""
    out = ""
    head = spans[0]
    if is_bold(head) and len(head["text"].strip()) <= 26 \
            and (mixed or head["color"] != body_color):
        out += "{{b}}" + head["text"].strip() + "{{/b}} "
        spans = spans[1:]
    return out + join_spans(spans)


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
            for l in block["lines"]:  # vertical runs are units of their own
                if not l["spans"] or not line_text(l).strip():
                    continue
                dx = l["dir"][0]
                if abs(dx) > 0.9:
                    continue
                u = rotated_unit(l, pno) if abs(dx) < 0.2 else None
                if u:
                    u["id"] = len(units)
                    units.append(u)
                else:  # oblique text needs a real text matrix; leave it in the source
                    skipped.append({"page": pno, "why": "oblique", "text": line_text(l)[:70]})
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
            # ink per role: a run-in label is often a different colour than the body it leads.
            # Classify by colour, never by weight - the Lancet summary box sets its BODY in a
            # bold-ish face too, so "is this span bold" would mislabel 700 chars as a label.
            all_spans = [sp for l in lines for sp in l["spans"] if sp["text"].strip()]

            def dominant(spans, fallback):
                if not spans:
                    return fallback
                cs = [(sp["color"], max(len(sp["text"].strip()), 1)) for sp in spans]
                return max({c for c, _ in cs}, key=lambda c: sum(w for x, w in cs if x == c))

            body_color = dominant(all_spans, all_spans[0]["color"])
            label_color = dominant([sp for sp in all_spans if sp["color"] != body_color],
                                   body_color)
            weights = [is_bold(sp) for sp in all_spans]
            mixed = any(weights) and not all(weights)
            solid_bold = all(weights)  # whole block set in a bold face (summary box, affiliations)
            chunks = [marked_line(l, mixed, body_color) for l in mains]
            for ln in lines:  # superscript citation markers glue onto their line
                if not sup(ln, size):
                    continue
                best, best_ov = None, 0.0
                for i, m in enumerate(mains):
                    ov = min(ln["bbox"][3], m["bbox"][3]) - max(ln["bbox"][1], m["bbox"][1])
                    if ov > best_ov:
                        best, best_ov = i, ov
                if best is not None:
                    chunks[best] += marked_line(ln, mixed, body_color).replace(" ", "")
            # a line that is entirely a short bold label stands alone in the original;
            # that is a layout fact, so it is recorded as a flag, never carried in the text
            chunks = [c.strip() for c in chunks if c.strip()]
            br_after_label = bool(chunks) and chunks[0].startswith("{{b}}") \
                and chunks[0].endswith("{{/b}}") and len(chunks[0]) <= 34
            body = tidy(" ".join(c for c in chunks if c))
            if not body:
                continue
            units.append({"id": len(units), "page": pno, "role": "prose",
                          "rect": [round(v, 2) for v in block["bbox"]],
                          "redact": [[round(v, 2) for v in l["bbox"]] for l in lines],
                          "size": size, "br_after_label": br_after_label,
                          "bold": solid_bold,
                          "color": body_color, "label_color": label_color,
                          "text": body})

    json.dump({"units": units, "skipped": skipped}, open(args.output, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(re.sub(r"\.json$", ".txt", args.output), "w", encoding="utf-8") as fh:
        for u in units:
            fh.write(f"[{u['id']}] {u['text']}\n")
    pr = sum(1 for u in units if u["role"] == "prose")
    ax = sum(1 for u in units if u["role"] == "axis")
    print(f"{len(units)} units ({pr} prose, {len(units) - pr - ax} cells, {ax} rotated), "
          f"{sum(len(u['text']) for u in units)} chars, {len(skipped)} blocks skipped -> {args.output}")
    print(f"translate in batches, then merge with merge_translations.py")


if __name__ == "__main__":
    main()
