# translate-paper

An agent skill that translates an academic paper PDF into Chinese **in place** — the two-column
text, the tables with their rules, the figure captions, the fonts and the colours all stay where
they were. The agent's own model is the translation engine: no API key, no translation service,
no paid backend.

```
paper.pdf ─extract_units→ units.json ─split_lanes→ N lanes ─subagents→ trans_*.json
        └─merge_translations(gate)→ translations.json ─fill_units→ paper.zh.pdf
        └─check_residual(gate) + audit_layout(gate) + make_dual(bilingual) + vision review
```

## Why not just run pdf2zh over the spans

The obvious implementation — extract the PDF's text spans, translate each, write them back —
produces fragments. In a real Lancet paper, 637 of 1365 spans are shorter than 15 characters and
sentences are cut across spans by the typesetter. This skill works on **paragraph blocks**, and
only drops to cell granularity where a block really is a table row.

## What it guarantees mechanically

| gate | catches |
|---|---|
| `MISS` | a unit nobody translated (a lane died) |
| `LATIN` | English left in the translation that the source unit never contained |
| `DRIFT` | one lane inventing its own synonym for a glossed term; the same source string getting two different renderings across lanes |
| `NUMBER` | a decimal or integer dropped while translating |
| `LEFTOVER` | source letters surviving inside a replaced rectangle |
| `OVERFLOW` | replacement running past its rectangle |
| `CLIPPED` | a deliberately-kept cell eaten by a neighbour's redaction |
| `BIoU` / `ANCHOR` / `SMALL` / `RULES` / `COLLIDE` | block structure drifting, mis-anchored text, sub-readable font size, lost table rules, text overlapping kept text |

Every gate has been run against a deliberately broken input to confirm it can go red — a green
gate that cannot fail is not a gate.

## Measured against zotero-pdf2zh (pdf2zh_next 2.8.2 + DeepSeek) on the same 8-page paper

| | zotero-pdf2zh | this skill |
|---|---|---|
| numeric tokens preserved | 539/551 (12 had `·` normalised to `.`; footnote `12.1%` typeset as `12 1%`) | 551/551 |
| table unit language | mixed (`8 yr` kept, `18个月`/`30 月`/`42 月` coexisting) | uniform |
| `yr=years. mo=months.` | rendered as "岁=年，月=月" | rendered correctly |
| reference list | translated (citations become unsearchable) | kept in the source language |
| bilingual output | yes | yes (`--mode page` or `--mode column`) |
| auto glossary | yes (651 terms) | you write `glossary.md` first; the gate enforces it |
| cost | API key + tokens | none |
| wall time | ~14 min | ~10 min with 6 parallel lanes |

## The review loop earns its keep

A vision-review subagent (protocol in `references/review-checklist.md`) reviewing the first
build of a real Lancet paper caught four defects the mechanical gates could not see:

- **a meaning-level escalation**: "shows no evidence that consumption ... has an adverse effect"
  (absence of evidence) had been translated as "显示…并无不良影响" (proof of no harm). Fixed in the
  lane file, and the checklist now names this failure mode explicitly.
- **wrong ink for whole paragraphs**: the extractor took a block's colour from its first span,
  which is the red run-in label, so black body text was reprinted in journal red. Fixed by
  storing body colour and label colour separately, each the dominant colour of the spans that
  carry it — classified by **ink, never by weight**, because the Lancet summary box sets its
  whole body in a bold face.
- **a bold body misread as a heading**: that same bold-only summary box made the extractor mark
  a random body word ("week.") as a run-in label, which the translator faithfully copied and
  the typesetter printed in label red. A block whose every span is bold now carries a
  block-level `bold` flag instead of per-span markers, and the typesetter keeps the face
  (bold) separate from the ink (label colour), so a forced-bold body cannot turn red.
- **table headers silently losing their weight**, because bold cells were rendered from
  translation text that carries no bold markers. Fixed by honouring the cell's own bold flag.
- **a Chinese word cut in half across a column break**, which English tolerates but Chinese does
  not. Fixed by rewording the sentence boundary.

All four became code or checklist entries, not one-off edits — the next paper inherits them.

## Bilingual form

`--mode column` crops each column and pairs it with its own translation, which keeps text at
100% size on two-column journals; `--mode page` puts whole pages side by side (the form
zotero-pdf2zh emits). Pages whose title, summary box or a wide table straddles the gutter fall
back to whole-page pairing instead of being cut mid-word.

## Install

```bash
npx skills add <owner>/translate-paper
```

Requires Python with PyMuPDF (`pip install pymupdf`); `fontTools` is optional and only used to
pull a single face out of a macOS `.ttc`. Output prefers a serif CJK face (Source Han Serif →
Songti SC → Noto Serif CJK → sans → PyMuPDF's built-in CJK).

## Limits

- No text layer, no go: scanned PDFs need OCR first.
- Rotated text inside figures is left in the source language.
- Bilingual output composes two PDFs side by side; it is not one page carrying both languages.
- Translation happens in parallel subagents, so `glossary.md` is the only thing keeping lanes
  consistent — write it before splitting lanes.

Not a substitute for a professional translation; it is built for reading a paper you have to
understand, fast.
