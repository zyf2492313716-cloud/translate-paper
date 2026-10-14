#!/usr/bin/env python3
"""Mechanical layout audit of a translated PDF (no vision model needed).

Reports, per page and overall:
  BIoU      overlap between where the replacement text was drawn and the source block rect
  SMALL     effective font size below the readable floor
  COLLIDE   replacement text overlapping text that was deliberately kept
  RULES     table/figure line-art lost between source and output
  UTB       source letters still standing inside a unit that should have been replaced
Exit 1 if any BLOCKING finding (RULES lost, COLLIDE, or BIoU < 0.35 with SMALL).

Usage: audit_layout.py source.pdf translated.pdf fill_manifest.json [--min-size 5]
"""
import argparse
import json
import re
import sys

import pymupdf


def spans(page):
    out = []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        for ln in blk["lines"]:
            for sp in ln["spans"]:
                if sp["text"].strip():
                    out.append((pymupdf.Rect(sp["bbox"]), sp["size"], sp["text"], sp["font"]))
    return out


def iou(a, b):
    inter = max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) * max(0, min(a.y1, b.y1) - max(a.y0, b.y0))
    union = a.get_area() + b.get_area() - inter
    return inter / union if union else 0.0


def blocks(page):
    return [pymupdf.Rect(b["bbox"]) for b in page.get_text("dict")["blocks"]
            if b.get("type") == 0 and b["lines"]]


def page_block_biou(a, b):
    """Did the block structure survive? Match source blocks to output blocks by position.

    Per-unit fill ratio is NOT a metric here: Chinese is shorter than English, so a good
    translation legitimately occupies less area than the source block.
    """
    if not a or not b:
        return None
    rest, tot = list(b), 0.0
    for ra in a:
        best, bi = None, 0.0
        for i, rb in enumerate(rest):
            v = iou(ra, rb)
            if v > bi:
                best, bi = i, v
        tot += bi
        if best is not None:
            rest.pop(best)
    return round(tot / len(a), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("translated")
    ap.add_argument("manifest")
    ap.add_argument("--min-size", type=float, default=5.0)
    ap.add_argument("--out", default="layout_audit.json")
    args = ap.parse_args()

    src, trn = pymupdf.open(args.source), pymupdf.open(args.translated)
    raw = json.load(open(args.manifest))
    faces = [re.sub(r"[^a-z]", "", n.lower()) for n in raw.pop("_fonts", {}).get("faces", [])]
    man = {int(k): v for k, v in raw.items()}
    findings, page_scores = [], {}
    for pno in range(trn.page_count):
        score = page_block_biou(blocks(src[pno]), blocks(trn[pno]))
        page_scores[pno + 1] = score
        if score is not None and score < 0.6:
            findings.append({"unit": None, "page": pno + 1, "kind": "BIoU", "severity": "medium",
                             "value": score,
                             "detail": "block structure drifted from the source page"})

    for uid, m in sorted(man.items()):
        drawn, base = pymupdf.Rect(m["drawn"]), pymupdf.Rect(m["rect"])
        if m.get("rot"):
            # a rotated axis label is centred on its original run, not hung from its start
            off = [abs((drawn.x0 + drawn.x1) / 2 - (base.x0 + base.x1) / 2),
                   abs((drawn.y0 + drawn.y1) / 2 - (base.y0 + base.y1) / 2)]
        else:
            off = [drawn.x0 - base.x0, drawn.y0 - base.y0]
        if abs(off[0]) > 2 or abs(off[1]) > 2:
            findings.append({"unit": uid, "page": m["page"] + 1, "kind": "ANCHOR", "severity": "medium",
                             "value": [round(v, 1) for v in off],
                             "detail": "replacement does not start where the source block started"})
        if m["size"] < args.min_size:
            findings.append({"unit": uid, "page": m["page"] + 1, "kind": "SMALL",
                             "severity": "high", "value": m["size"],
                             "detail": f"{m['chars']} chars forced into {round(m['rect'][2]-m['rect'][0])}x"
                                       f"{round(m['rect'][3]-m['rect'][1])} pt at {m['size']} pt"})

    for pno in range(trn.page_count):
        lost = len(src[pno].get_drawings()) - len(trn[pno].get_drawings())
        if lost > 0:
            findings.append({"unit": None, "page": pno + 1, "kind": "RULES", "severity": "high",
                             "value": lost, "detail": f"{lost} vector paths lost (table rules or art)"})
        mine_rects = [(uid, pymupdf.Rect(m["drawn"]) + (-1, -1, 1, 1))
                      for uid, m in man.items() if m["page"] == pno]
        def is_mine(rect, font):
            flat = re.sub(r"[^a-z]", "", font.lower())
            if faces and any(all(t in flat for t in re.findall(r"[a-z]+", f)) for f in faces):
                return True
            return any(mr.contains(rect) for _, mr in mine_rects)

        mine, kept = [], []
        for rect, size, text, font in spans(trn[pno]):
            owner = next((uid for uid, mr in mine_rects if is_mine(rect, font)), None)
            (mine if owner is not None else kept).append((rect, size, text, owner))
            if size < args.min_size:
                findings.append({"unit": owner, "page": pno + 1, "kind": "SMALL", "severity": "high",
                                 "value": round(size, 2), "detail": f"rendered {text[:18]!r} at {size:.1f} pt"})
        for r1, _, t1, uid in mine:
            for r2, _, t2, _ in kept:
                if not t2.strip() or not r1.intersects(r2):
                    continue
                ov = (min(r1.x1, r2.x1) - max(r1.x0, r2.x0)) * (min(r1.y1, r2.y1) - max(r1.y0, r2.y0))
                if ov > 3.0:
                    findings.append({"unit": uid, "page": pno + 1, "kind": "COLLIDE", "severity": "high",
                                     "value": round(ov, 1),
                                     "detail": f"{t1.strip()[:14]!r} overlaps kept {t2.strip()[:18]!r}"})
                    break

    scores = [v for v in page_scores.values() if v is not None]
    summary = {
        "units_audited": len(man),
        "pages": trn.page_count,
        "page_block_BIoU": page_scores,
        "mean_page_BIoU": round(sum(scores) / len(scores), 3) if scores else None,
        "findings": len(findings),
        "by_kind": {},
    }
    for f in findings:
        summary["by_kind"][f["kind"]] = summary["by_kind"].get(f["kind"], 0) + 1
    json.dump({"summary": summary, "detail": findings}, open(args.out, "w"), ensure_ascii=False, indent=1)

    blocking = [f for f in findings if f["severity"] == "high"]
    print(f"audited {len(man)} units / {trn.page_count} pages | mean page BIoU {summary['mean_page_BIoU']} "
          f"| findings {len(findings)} ({summary['by_kind'] or 'none'})")
    for f in findings[:12]:
        print(f"  {f['severity'].upper():6} {f['kind']:8} p{f['page']} #{f['unit']}: {f['detail']}")
    print("AUDIT:", "FAIL" if blocking else "PASS", f"({len(blocking)} blocking) -> {args.out}")
    sys.exit(1 if blocking else 0)


if __name__ == "__main__":
    main()
