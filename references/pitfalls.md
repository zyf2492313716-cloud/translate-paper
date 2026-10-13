# Engineering traps in this pipeline

Each of these produced a wrong-looking result once. The code already encodes the fix;
re-read this before changing the geometry, redaction or font logic.

**Redaction rect must not expand horizontally.** `Rect(r) + (-0.4, -0.4, 0.4, 0.4)` ate the
first letter of the neighbouring table cell ("vs" rendered as a stray "s"). Expand only in y.

**Table rows are blocks of one-line cells, not multi-span lines.** Deciding "is this a table"
by counting spans per line misfires; group lines into visual rows by y-overlap and require a
row to hold side-by-side items in a large share of rows. Superscript citation markers must be
excluded from that test first, otherwise every justified paragraph looks like a table.

**Tokenizer needs a space break for latin runs.** If a pure-Latin string becomes one token
(author lists, "MSC 2088"), it cannot wrap and silently overflows the column. CJK breaks
anywhere, latin breaks only at spaces.

**Cell height tolerance.** A single-line cell's bbox is ~1.0x the font size; requiring
`size * 1.15` forces every one-line cell to shrink. Use `size * 1.05` plus ~1.5 pt slack, and
let a `cell` borrow up to 4 pt of the inter-column gap before shrinking.

**CFF subsetting can fail silently.** `doc.subset_fonts()` on Source Han leaves a 28 MB file
and prints "Reserved charstring byte". Use `subset_fonts(fallback=True)` (fontTools path).

**Serif vs sans changes the fit.** Font preference must be serif-first (Source Han Serif →
Songti SC face extracted from `.ttc` → sans); switching to a sans face reflowed the author
line and pushed it below 0.8x.

**Gates must be able to go red.** Validate `check_residual.py` against the source PDF (it must
fail roughly once per unit) and inject one stray English word to confirm the merge gate fails.
Probe rectangles for the residual check use the character's left edge inside
`[x0-0.5, x1-0.2]`; centre-based probing produced false positives from adjacent cells.

## Parallel lanes

**The drift gate must judge prose units only.** Cells legitimately shorten: a lane holding
`Emotional` must render `情绪`, not `情绪症状`. Matching only the first word of a glossary entry
(`emotional`) made every cell lane look guilty. Match the whole phrase, over `prose` units, and
skip entries shorter than 4 characters.

**A lane's long wording surfaces as `below 0.8x`, not as a failure.** One lane answered
`Council` with `市政公租房` in a 21 pt column; every gate stayed green while the typesetter
quietly shrank it to 4·5 pt. Read the fill output and shorten the cell.

**Re-dispatch a lane; do not translate in the main thread.** Lane files are independent, so a
failed or drifting lane is re-run alone. Editing its JSON by hand pulls thousands of characters
of translation into the main context and makes the fix unattributable.

