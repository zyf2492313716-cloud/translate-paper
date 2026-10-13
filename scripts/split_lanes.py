#!/usr/bin/env python3
"""Split units.json into balanced translation lanes for parallel subagents.

Long units (paragraphs) go into `--lanes` prose lanes balanced by character count;
short units (table cells, labels) go into their own lanes balanced by item count,
because a lane of 200 three-word cells is cheap but needs many JSON keys.

Writes work/src_<n>.txt (lines "[id] source") and work/ids_<n>.txt per lane.
"""
import argparse
import json
import math
import os

CELL_MAX = 60


def pack(items, lanes, key):
    total = sum(key(i) for i in items)
    per = total / lanes
    out, cur, s = [], [], 0.0
    for it in items:
        cur.append(it)
        s += key(it)
        if s >= per and len(out) < lanes - 1:
            out.append(cur)
            cur, s = [], 0.0
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("units")
    ap.add_argument("--dir", default="work")
    ap.add_argument("--lanes", type=int, default=5, help="prose lanes")
    ap.add_argument("--cell-lanes", type=int, default=2, help="lanes for short cells")
    args = ap.parse_args()

    units = json.load(open(args.units))["units"]
    big = [u for u in units if len(u["text"]) > CELL_MAX]
    small = [u for u in units if len(u["text"]) <= CELL_MAX]
    lanes = pack(big, max(1, min(args.lanes, math.ceil(len(big) / 3))), lambda u: len(u["text"]))
    lanes += pack(small, max(1, min(args.cell_lanes, math.ceil(len(small) / 40))),
                  lambda u: len(u["text"]))
    os.makedirs(args.dir, exist_ok=True)
    for i, lane in enumerate(lanes, 1):
        with open(os.path.join(args.dir, f"src_{i}.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(f"[{u['id']}] {u['text']}" for u in lane) + "\n")
        with open(os.path.join(args.dir, f"ids_{i}.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(str(u["id"]) for u in lane) + "\n")
        print(f"lane {i}: {len(lane)} units, {sum(len(u['text']) for u in lane)} chars")
    print(f"{len(lanes)} lanes -> {args.dir}/src_*.txt ; each subagent writes {args.dir}/trans_<n>.json")


if __name__ == "__main__":
    main()
