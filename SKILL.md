---
name: translate-paper
description: Translate an academic paper / journal PDF into Chinese (or another language) while keeping the original layout - two-column text blocks, tables with their grid and rules, figure captions, fonts and colours. The agent's own model is the translation engine, so no API key, no external translation service and no paid backend is needed. Use when the user asks to 翻译论文/文献翻译/把这篇 PDF 翻译成中文/translate this paper, or wants a readable Chinese copy of a paper PDF that still looks like the paper. Not for scanned/image-only PDFs (OCR first), not for editable Word rebuilds, not for single-paragraph glosses.
---

# Translate a paper PDF in place

Paragraph-level replacement: extract translatable units, the agent translates them,
a typesetter erases only the source glyphs and re-wraps the translation into the same
rectangles. Tables stay tables; figures, rules and background shading stay untouched.

Scripts live in `scripts/`. All need only PyMuPDF (`pip install pymupdf`);
`fontTools` is optional and used to pull one face out of a `.ttc`.

## Workflow

### 1. Extract units

```bash
python scripts/extract_units.py paper.pdf -o work/units.json
```

Produces `work/units.json` (with `rect`, `redact`, `size`, `color`, `role`) and a flat
`work/units.txt` for reading. Roles: `prose` = one paragraph, `cell` = one table cell,
`axis` = one rotated run inside a figure (y-axis labels), which carries `rot` and is drawn
back with the same rotation. Running head/footer and the reference list are dropped into
`skipped`. Pass `--keep-refs` only if the user really wants the bibliography translated.

What the extractor records about ink, and why it is not just "the colour of the block":

- `color` / `label_color` — body ink and run-in-label ink, each the **dominant** colour of
  the spans that carry it. Never the first span's colour (that is usually the label), and
  never decided by weight: the Lancet summary box sets its whole *body* in a bold face,
  so "is this span bold" mislabels 700 characters as a heading.
- `bold` — the whole block is set in a bold face (summary box, affiliations). The
  translation then renders in the bold face without needing markers in the text.
- `br_after_label` — the label stands alone on its own line in the original, so the
  typesetter breaks after it. Layout facts travel as flags, never as text markers.

A `{{b}}...{{/b}}` marker in a unit's text therefore means exactly one thing: this span
is a run-in label, and it is rendered in `label_color`.

### 2. Glossary, lanes, subagents

Do **not** translate inside the main thread — a 30 000-character paper is 25 000 tokens of
output you will never reuse. Write the term contract, split into lanes, and let subagents
translate in parallel; the main context only sees file paths and one-line receipts.

```bash
python scripts/split_lanes.py work/units.json --dir work --lanes 5 --cell-lanes 2
```

Before splitting, write `work/glossary.md` as `english,中文` lines (one term per line, e.g.
`suboptimum outcome,欠佳结局`). Every lane gets this file, and the merge gate uses it to catch a
lane that invented its own synonym.

Then dispatch one subagent per `work/src_N.txt` **in a single message** (parallel), each with
the rules below pasted in, telling it to read its source file plus the glossary and to write
only `work/trans_N.json` (`{"id": "译文"}`, plain UTF-8 JSON), replying with one line
`lane N: wrote M entries`. Keep short-cell lanes on a separate prompt that stresses column
widths — give the agent the actual narrow-cell renderings you want (`18 mo`→`18月`).

Rotated figure labels go in the **last** lane, and their prompt is different again: their
budget is the vertical advance of the original run, which `split_lanes.py` prints next to each
one (`可用竖直长度 62.8 pt / 字号 6.43 ≈ 9 个汉字`). Tell that lane a Chinese character counts
as 1 glyph and a Latin letter, digit, space or bracket counts as about 0.5, and that going over
budget shrinks the label into illegibility.

If a lane returns nothing, times out, or the gate names it: re-dispatch **that lane only**.
Lane files are independent, so nothing else is lost. Never edit a lane's output into the
main thread's reply.

Rules that must appear verbatim in every lane prompt:

- **Numbers, units and statistical symbols are copied, never retyped from memory.**
  Keep the source's own decimal style **per token**: journals often mix separators on purpose
  (Lancet: `12·1%` inside a table, `12.1%` in its footnote). Never unify them. Same for CIs,
  p-values, `n=`, and the journal's own typos — fixing a source typo is a factual edit you
  were not asked for.
- **Keep citation markers where they sat.** `...major source.1 Low intake...` → `...主要来源1。低摄入...`.
- **Terminology comes from `work/glossary.md`, not from the translator's taste.**
  Annotate the Chinese term with the English original in parentheses on first occurrence only.
- **Wrap run-in headings exactly as extracted.** If the source unit carries `{{b}}...{{/b}}`,
  keep those markers around the matching translated words; they render bold.
- **Short cells need short Chinese.** A 17 pt column cannot hold `18月龄`; use `18月`.
  `merge_translations.py` applies such table-cell auto-forms; extend its `AUTO` list.
- **Never leave stray English that the source did not have** (glosses you chose to keep are
  fine — the gate allows any token that also appears in the source unit).

### 3. Merge and gate

```bash
python scripts/merge_translations.py work/units.json "work/trans_*.json" \
       --glossary work/glossary.md --allow work/allow.txt
```

Fails (exit 1) on:
`MISS` a unit with letters nobody translated; `LATIN` an English token the unit's own source
never contained (a leftover); `DRIFT` a lane whose sources use a glossed term ≥2 times while
its output never uses the mandated Chinese — that is how parallel lanes silently diverge.

Cross-column abbreviations (`WISC`, `SDQ`, `MeHg`) legitimately survive into a later
paragraph's translation: list those in `work/allow.txt` (one per line) so the gate separates
deliberate retention from a leftover. Never delete a check to make it pass.

### 4. Typeset

```bash
python scripts/fill_units.py paper.pdf translations.json work/units.json paper.zh.pdf
```

Redacts text only (`graphics=LINE_ART_NONE`, `images=NONE`), then fits each block by shrinking
the font down to 0.5x and never letting closing punctuation lead a line. Font resolution order:
Source Han Serif / Noto Serif CJK on the system → a serif face pulled out of `Songti.ttc` →
sans → PyMuPDF's built-in CJK. Report which one was used if the user cares about type.

The run prints `N units below 0.8x: [(id, factor), ...]`. Each such id is a wording problem,
not a font problem: shorten that cell (re-dispatch the lane or edit its file) and re-run.
Chasing it with more shrinking produces text nobody can read.

Rotated units are redrawn with `insert_text(..., rotate=...)`, centred on the original run.
A y-axis label reads bottom-to-top and its column is only ~6 pt wide, so the anchor comes from
the extents the chosen face actually produces (measured once per size), not from font metrics.

When a unit fits at no size down to 0.5x, `fill_units.py` prints it as `DROPPED` and exits 1.
Do not treat that as a warning: the redaction pass already erased the source text, so a dropped
unit is a hole in the page that `LEFTOVER` physically cannot detect.

### 5. Verify before calling it done

```bash
python scripts/check_residual.py paper.zh.pdf work/units.json translations.json
```

Three gates: `LEFTOVER` (source letters survived inside a replaced unit), `OVERFLOW`
(replaced text runs past its rectangle), `CLIPPED` (a unit you deliberately kept in English
got partly eaten by a neighbour's redaction). All must read `GATE: PASS`.

Then diff the numbers per page — this is the check that matters for a research paper:

```python
RX = re.compile(r"(?<![\d·.])(\d+(?:[·.]\d+)+)(?![·.\d])")   # per page, whitespace kept
# a real loss looks like: source '12.1%' -> output '12 1%' (point dropped while typesetting)
```

Also audit the layout mechanically (needs the manifest `fill_units.py` wrote):

```bash
python scripts/audit_layout.py paper.pdf paper.zh.pdf work/fill_manifest.json
```

`BIoU` compares **block structure page by page** (source blocks vs output blocks) — never
per-unit area, because Chinese legitimately occupies less area than English. `ANCHOR` catches a
replacement that no longer starts where its block started. `SMALL` flags text below the
readable floor. `RULES` flags vector paths (table rules, art) lost to redaction. `COLLIDE`
flags replacement text overlapping text that was deliberately kept; it classifies by **font**,
so the inserted font names must stay in the manifest.

Prove the gates are not vacuous: run `check_residual.py` against the **source** PDF (must FAIL
roughly once per unit); run `audit_layout.py --min-size 8` (must go red); edit one lane file to
swap a glossary term for a synonym or drop a digit (the merge gate must name that lane). A
green gate that cannot go red is not a gate.

### 6. Bilingual version, when asked for one

```bash
python scripts/make_dual.py paper.pdf paper.zh.pdf paper.dual.pdf --mode column
```

`--mode column` crops each column and pairs it with its own translation — the right form for
two-column journals, since text stays at 100% and the eye does not cross a 1200 pt sheet.
`--mode page` (whole page beside whole page) matches what zotero-pdf2zh's `*.dual.pdf` looks
like. Both fall back to whole-page for any page whose title, summary box or wide table straddles
the gutter; running head/footer furniture is ignored when deciding.

### 7. Vision review loop (subagent, not the main thread)

Dispatch a subagent with [references/review-checklist.md](references/review-checklist.md) and
the rendered pages; it appends findings to `work/defects.json` **page by page**, so an
interrupted run still leaves its work on disk. Then:

1. fix `high`/`medium` items by editing the **lane file** that owns the unit and re-running
   merge → fill → audit (never patch the PDF itself),
2. re-review only the pages that changed,
3. stop when a pass returns no `high`; report the `low` items you chose not to fix.

A lane that came back `failed` may still have produced its artifacts — look at the disk before
re-dispatching, and re-dispatch the narrow remaining work rather than the whole review.

## Known limits

- Scanned / image-only PDFs have no text layer: run OCR first.
- Axis-aligned rotated runs (90°/270°, the usual y-axis labels) are translated and redrawn
  rotated. Genuinely oblique text is left in the source language and reported as `oblique`
  in `skipped` - drawing it needs a text matrix, which the typesetter does not do.
- Very dense tables may need a shorter cell wording rather than more shrinking; the floor is
  0.5x and anything below that is reported as `below 0.8x`.
- Bilingual output is a **side-by-side composition** of the two PDFs (`make_dual.py`), not a
  single page carrying both languages; paragraph-interleaved Markdown is a different job.
- `--mode column` cannot split a page whose title/summary/table straddles the gutter; such
  pages come out as whole-page pairs instead, so a document can mix both page widths.

Before changing extraction granularity, redaction geometry or font resolution, read
[references/pitfalls.md](references/pitfalls.md) — each entry there cost one broken render.

