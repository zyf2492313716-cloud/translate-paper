# Visual review protocol (what machines cannot see)

Run this as a **subagent**, not in the main thread. Render pages to PNG at 130–160 dpi and
read the images. Write findings **incrementally**: append to `work/defects.json` after each
page so a dropped connection or turn limit still leaves the work on disk.

Mechanical gates (`check_residual.py`, `audit_layout.py`) already cover overflow, collisions,
lost table rules, tiny fonts and lost numbers. Do not spend the review on those. Look for:

| # | Category | What to look for |
|---|---|---|
| 1 | `readability` | Text that fits its box but reads badly: rivers of white, one word per line, a paragraph that ends with a lone punctuation mark on its own line |
| 2 | `anchoring` | Replacement that starts visibly lower/righter than its neighbours, breaking the column's left edge |
| 3 | `semantic` | Spot-check 3 passages against the source: does the Chinese say what the English said, or did a lane paraphrase away a qualifier ("could"→"会", "no evidence that"→"证明没有") |
| 4 | `terminology` | The same concept rendered two different ways across pages (the merge gate only catches *identical* source strings) |
| 5 | `figure_table` | A caption that no longer visually belongs to its figure; a table cell whose meaning shifted columns |
| 6 | `bilingual` | For `--mode column` output: a half-page cut mid-block because a title/table straddles the gutter; left/right halves that no longer line up |
| 7 | `style` | Bold run-in labels lost their bolding; body serif replaced by sans |

Deliberate, not defects: untranslated reference list, rotated axis labels inside figures,
the journal's own typos, Lancet-style `1·48` decimals.

Output shape, one entry per finding, written after every page:

```json
{"page": 3, "category": "semantic", "severity": "high|medium|low",
 "unit": 63, "where": "结局指标 第2句", "detail": "…what you saw…", "suggest": "…short fix…"}
```

`severity=high` only for wrong meaning, lost qualifier, or a reader being misled. Layout
niggles are `low`. A review that returns 40 `high` findings is usually miscalibrated.
