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

## Rotated figure labels

**Only axis-aligned runs can be redrawn.** A rotated label is extracted by its direction vector,
but 90°/270° is the only family `insert_text(rotate=...)` can produce. A genuinely oblique run
must be left in the source language and recorded in `skipped` as `oblique`, or it comes back
horizontal and lies across the figure.

**Do not derive the rotated anchor from font metrics.** Ascender ratios differ per face, and the
probe string itself changes the box. Insert a probe into a scratch page at the size you are about
to use, measure how far the produced bbox grows either side of the anchor, and solve for the
anchor from the measured extents. Centre the run on the original one: the two-line y-axis label
(`Social development` over `children suboptimum (%)`) is a construct centred on the axis, so
bottom-aligning it reads as a different label.

**A column window alone will not isolate a rotated run.** A y-axis label sits in a ~6 pt x strip
that the page's horizontal body text crosses at other heights, so "all chars whose x-centre is
inside the rect" returns the whole page's strip. Filter by line direction first, then by box.

**`ANCHOR` means something else for rotated units.** Prose hangs from the block's start; a rotated
label is centred on its original run. Comparing the drawn corner to the rect corner reports six
false MEDIUMs on one Lancet figure page. Compare centres for rotated units.

**A dropped unit is invisible to `LEFTOVER`.** When nothing fits down to 0.5x the unit is not
redrawn, but the redaction pass already erased the source text, so there are no source letters
left to find and the gate stays green on a page with a hole in it. `fill_units.py` therefore
prints `DROPPED` and exits 1: the stage that erases has to be the one that refuses to claim
success. Verified both ways on the Lancet build, and by injecting an over-long label
(`GATE: FAIL`, exit 1) against the clean build (exit 0).


## Mixed script, borrowed whitespace, and the number that was really a footnote

**A serif that carries Chinese draws Latin about a third wider than the journal's condensed
sans.** The sidebar string `UNDP–UNFPA–UNICEF–WHO–` measured 106 pt against an 83 pt column and
overflowed at *every* size, because it has no space to break at. Route Latin (and the marks that
live inside Latin words) to a real Latin face; keep `—— … “”` on the CJK face, because Helvetica
draws them short and low and it reads as a hole in the sentence.

**An unbreakable token needs a mid-token break, not a smaller font.** Bare URLs and DOIs are the
common case; every browser breaks them too. Shrinking one to 0.5x to stay inside the box produces
text nobody can read.

**A unit's rect is its own ink box, not the column it sits in.** A table row header occupies 17 pt
of a 116 pt column, and a figure legend key has the gap to its swatch on one side. Let a
single-line unit borrow the whitespace beside it, bounded by the nearest *ink* (text line or vector
path) that sits wholly to that side. Grow right first so a cell keeps its column's left edge; grow
left, and right-align at the original edge, only when the right side cannot reach 0.8x.

**"No obstacle on that side" is not "no room on that side".** Mapping an empty side to a zero
budget silently disabled the whole feature; every label stayed at 0.7x and every gate stayed green.
An open side means the cap, and the cap is still bounded by the page edge.

**Borrow only what the line needs.** A box grown to the cap reaches the next column, and the
residual checks then read that column's ink as this unit's overflow: three false reds, one of them
207 pt wide, all traced to a 5 pt deficit.

**Superscript references glue onto the number before them in the text layer.** `one in 116.2` is
116 plus reference 2, and `20% in 20064` is 2006 plus reference 4. Split them at extraction, using
the span's size and raised baseline, or a translator copies `116.2` as a statistic, and the number
gate demands the literal `20064` survive a faithful translation. Detect the glue from geometry, not
from a wording pattern: Lancet writes real decimals with `·` and its SDG target `3.1` is genuine.

**The gate that follows a mid-token break must forgive its fragments.** After breaking a URL, the
next line reads back as `e。`, so `LEFTOVER` has to exempt any run that is a strict prefix or
suffix of a longer word the translation kept. Same reason the number gate now flags a digit run the
source never wrote that *splits* into two runs it did: that is `ICD-11` + ref `20` becoming
`ICD-1120`, which a reader takes for a code.

**Overflow is per line, and a line is yours only if it starts on your left edge and sits inside
your vertical span.** The original check looked at the block's bottom line, so a multi-line block
whose first line crossed the column was invisible; the naive fix (any line overlapping the band)
blamed a table row's long first line on the unit that held its last word.
