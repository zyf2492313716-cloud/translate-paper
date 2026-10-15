#!/usr/bin/env python3
"""Merge per-batch translation JSONs into translations.json and gate the result.

Gates (exit 1 unless all pass):
  MISS   a unit containing letters has no translation and no auto rule
  LATIN  the translation carries an English word that the source unit did not
         contain (ie. untranslated leftovers / accidental English)
Usage: merge_translations.py units.json trans_1.json [trans_2.json ...]
"""
import argparse
import glob
import json
import os
import re
import sys

MARK = re.compile(r"\{\{/?b(?:r)?\}\}")
LATIN = re.compile(r"[A-Za-z]{2,}")

# short in-table forms; extend as needed for the target language
AUTO = [
    (re.compile(r"^(\d+)\s*yr$"), r"\1岁"),
    (re.compile(r"^(\d+)\s*m[oa]$"), r"\1月"),
    (re.compile(r"^(\d{1,2})\s*(weeks|wk)$"), r"\1周"),
]


def load_allow(path):
    """Tokens the agent deliberately keeps in the source language (glossary / abbreviations)."""
    if not path or not os.path.exists(path):
        return set()
    return set(LATIN.findall(open(path, encoding="utf-8").read()))


def load_glossary(path):
    """Lines shaped `english,中文` (see SKILL.md). Returns [(en, zh), ...]."""
    if not path or not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        if "," in line and not line.startswith("#"):
            en, zh = line.split(",", 1)
            en, zh = en.strip(), zh.strip()
            if en and zh:
                out.append((en, zh.split("（")[0].split("/")[0].strip()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("units")
    ap.add_argument("batches", nargs="+")
    ap.add_argument("--allow", default="allow.txt",
                    help="file listing abbreviations you intentionally keep (one per line)")
    ap.add_argument("--glossary", default="glossary.md",
                    help="csv lines 'english,中文' used to detect a lane inventing its own term")
    ap.add_argument("--out", default="translations.json")
    args = ap.parse_args()

    units = json.load(open(args.units))["units"]
    trans, seen_batches = {}, {}
    for name in args.batches:
        files = sorted(glob.glob(name)) if any(c in name for c in "*?[") else [name]
        for f in files:
            batch = {int(k): v for k, v in json.load(open(f)).items()}
            seen_batches[f] = list(batch)
            trans.update(batch)

    allow = load_allow(args.allow)
    by_id = {u["id"]: u for u in units}
    missing = []
    for u in units:
        if u["id"] in trans:
            continue
        for rx, rep in AUTO:
            if rx.match(u["text"].strip()):
                trans[u["id"]] = rx.sub(rep, u["text"].strip())
                break
        else:
            if re.search(r"[A-Za-z]{3}", u["text"]):
                missing.append(u)

    bad = []
    for uid, text in trans.items():
        src = MARK.sub("", by_id[uid]["text"])
        for tok in set(LATIN.findall(MARK.sub("", text))):
            if tok in allow:
                continue
            if not re.search(r"(?<![A-Za-z])" + re.escape(tok) + r"(?![A-Za-z])", src, re.I):
                bad.append((uid, tok))

    drift = []
    gloss = load_glossary(args.glossary)
    for path, ids in seen_batches.items():
        # cells legitimately use short forms ("情绪" for "emotional symptoms"): prose only
        prose = [i for i in ids if i in by_id and by_id[i]["role"] == "prose"]
        srcs = " ".join(by_id[i]["text"] for i in prose)
        blob = json.dumps({str(k): trans[k] for k in ids if k in trans}, ensure_ascii=False)
        for en, zh in gloss:
            words = en.split("(")[0].strip().lower().split()
            phrase = r"[\s,.]+".join(re.escape(w) for w in words if w)
            if len(en.split('(')[0].strip()) < 4:
                continue
            if len(re.findall(phrase, srcs, re.I)) >= 2 and zh not in blob:
                drift.append((os.path.basename(path), en, zh))

    # the same source string must never get two renderings (this is how lanes diverge)
    seen_map = {}
    for uid, text in sorted(trans.items()):
        key = by_id[uid]["text"]
        if key in seen_map and seen_map[key] != text and len(key) > 3:
            drift.append(("(cross-lane)", key[:26], seen_map[key][:18] + " / " + text[:18]))
        seen_map.setdefault(key, text)

    # every decimal and every >=2-digit integer in a source unit must survive translation
    numloss = []
    for uid, text in trans.items():
        src = MARK.sub("", by_id[uid]["text"])
        keep = MARK.sub("", text).replace(" ", "")
        for tok in set(re.findall(r"\d+(?:[·.]\d+)+|(?<![\d·.])\d{2,}(?![\d·.])", src)):
            if tok not in keep:
                numloss.append((uid, tok))

    # a reference number glued onto the year that follows it reads as one six-digit statistic:
    # 'deaths.24 In 2015' must not come out as '死亡。242015年'. The source never contains that
    # concatenation, so its absence on both sides is what makes this check precise.
    glue = []
    for uid, text in trans.items():
        src = MARK.sub("", by_id[uid]["text"])
        src_runs = set(re.findall(r"\d+", src))
        for run in sorted(set(re.findall(r"(?<!\d)\d{3,}(?!\d)", MARK.sub("", text)))):
            if run in src_runs:
                continue
            # a number the source never wrote that splits into two numbers it did: that is a
            # reference marker glued onto the figure beside it (ICD-11 + ref 20 -> ICD-1120)
            if any(a in src_runs and b in src_runs for a, b in
                   ((run[:i], run[i:]) for i in range(1, len(run)))):
                glue.append((uid, run))

    print(f"units={len(units)} translated={len(trans)} missing={len(missing)} "
          f"allowlist={len(allow)} glossary={len(gloss)}")
    for u in missing:
        print(f"  MISS [{u['id']}] p{u['page']+1} {u['role']}: {u['text'][:70]}")
    for uid, tok in sorted(bad):
        print(f"  LATIN [{uid}] {tok} :: {by_id[uid]['text'][:40]!r} -> {trans[uid][:40]!r}")
    for path, en, zh in drift:
        print(f"  DRIFT {path}: '{en}' -> {zh}")
    for uid, tok in sorted(numloss)[:30]:
        print(f"  NUMBER [{uid}] lost {tok!r} :: {by_id[uid]['text'][:32]!r} -> {trans[uid][:32]!r}")
    for uid, tok in sorted(glue)[:30]:
        print(f"  GLUE [{uid}] {tok!r} :: {by_id[uid]['text'][:32]!r} -> {trans[uid][:32]!r}")
    if missing or bad or drift or numloss or glue:
        print("GATE: FAIL  (keep abbreviations in", args.allow,
              "; re-dispatch drifting lanes, restore lost numbers - never edit the gate)")
        sys.exit(1)
    json.dump({str(k): trans[k] for k in sorted(trans)},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"GATE: PASS -> {args.out}")


if __name__ == "__main__":
    main()
