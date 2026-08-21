# Why the slides show wrong numbers and unreadable text — root cause report

Date: 2026-08-20 · Instance: local `0.0.543.18` (:8095) · Checked by: four parallel
validators, one per deck, each re-computing every number on every slide and in the
chat against the real data, and reading the rendered pages with their own eyes.
Nothing was fixed or changed — this is analysis only.

The four decks checked (all generated today through the chat API with ordinary,
non-technical prompts):

| # | Source | Report |
|---|--------|--------|
| 1 | Uploaded excel (branch sales) | `4f7b3f65-758e-4d0f-9161-1e649d327a90` |
| 2 | Power BI | `e021a7cf-1e5a-46eb-a64d-91f2b5d52faf` |
| 3 | Microsoft Fabric | `0515727a-cee5-49b0-9915-e051735f3a10` |
| 4 | City Mart Retail (local database) | `68e4dfc7-22f2-476f-8705-8a5642c9efb8` |

---

## The short version

The user is right: the data on the slides is not trustworthy, and the Fabric deck
really does have invisible headings. Five separate causes, ranked by damage:

**1. The slide-building step types numbers from memory instead of copying them
from the query results.** This is the single biggest cause. The system first runs
real queries and gets correct answers. Then a second step writes the code that
draws the slides — and that code does not read the query results at all. It
contains the numbers typed out by hand, from the model's short-term memory of the
conversation. When the memory is good, the slides are right (Power BI deck: every
number happened to match). When it is not, the slides are wrong and nothing
catches it (excel deck: 20+ wrong numbers; Fabric deck: 7 of 12 months of a
revenue chart simply made up — the generated code even contains the comment
"Sample seasonal monthly values"). The made-up numbers are self-consistent —
totals sum, percentages recompute — so the deck never looks wrong from inside.

**2. Old data is presented as current, and nobody is told.** The user asked
"last month" / "this year" in August 2026. The retail database ends December 2025,
the Power BI dataset ends January 2025, and Fabric has no 2026 rows at all — its
"how is this year going" question was silently answered with 2025 and 2023 data
under a big "STAFF TOWN HALL 2026" banner. In the Fabric run the one query whose
job was to check the date range failed, two "2026" queries came back with zero
rows, and the system still recorded them as successful and moved on. At no point
does any slide or chat message say "note: the data ends at …".

**3. White text on a white sheet.** The user's "blurry / no heading / light text"
complaint on the Fabric deck. The model built its cover and closing slide with a
dark navy background and white text. The theme layer then lays its own plain
white sheet over the whole slide — on top of the model's background, underneath
the model's text — and never re-colors the text. Result: white words on a white
sheet, contrast exactly 1.00:1, literally invisible. The Fabric cover title
("State of City Mart: Together We Win") and the closing slide title are the two
victims, and the Power BI deck is hit far worse: the model made ALL its slides
dark there, so the main heading of every content slide (2, 3, 4 and 5) is
invisible. The slide-7 "clipped by a circle" effect is the same bug seen
sideways: the only readable fragment of the title is the part that happens to sit
on a navy decoration; the rest is white-on-white.

**4. Confident business claims with no data behind them.** Sprinkled across all
four decks, written in the same voice as the verified figures, so a reader cannot
tell them apart:
- Retail: "members contribute 78% of revenue" — the real number is 41%; the deck
  then tells management to chase walk-ins as a "22%" niche when walk-ins are
  actually the larger half (59%).
- Fabric: "90%+ compliance pass rate" — the real pass rate is about 52%.
- Power BI: "$4.8B full-year target" — no target exists anywhere in the data.
- Plus invented storylines ("all-time high", "lost share to newly expanded
  stores", "supply chain disruptions") that no field in any dataset can support.

**5. The safety checks measure the wrong things.** A layout checker ran on every
deck — and on the two broken ones it flagged only six harmless decorative circles
poking off the page edge, while missing every invisible title. It measures
geometry, not readability. Meanwhile the "blur" is not in the files at all: the
PPTX files are pure vector and razor sharp. The soft look on screen comes from
the preview pictures, which are generated at one fixed size (2000px) and then
stretched by high-resolution (retina) displays. The thumbnail the user sees in
chat is the broken white cover, which makes the whole deck look empty and washed
out before it is even opened.

One more pattern worth naming: **the chat answer is consistently more honest than
the deck built from it.** In the excel run the chat text was 100% correct while
the slides were full of fabrications. In the Fabric run the chat carefully said
"202.3M was April 2023, 187.3M was April 2025" — the deck kept only the bigger
number and dropped the year. Errors are introduced at the moment slides are
generated, not when the data is analysed.

---

## Deck-by-deck detail

### 1. Excel deck — correct analysis, fabricated slides

The query step and the chat answer were both perfect. The slides were not:

- Slide 4 scorecard: Riverside and North Point rows almost entirely wrong
  (Riverside total shown 1,995 vs real 1,459; North Point 1,705 vs real 1,922),
  which flips their ranking — the deck puts Riverside 4th, reality puts it 5th.
  Junction Sq's January shown as 402, a number that appears nowhere in the file.
- Network totals wrong: revenue 12,827 vs real 12,473; transactions 352,747 vs
  real 342,998.
- The fabrications are disguised: transaction counts were back-calculated from
  the invented revenue at the true ratio (invented total × 27.5), so every check
  inside the deck balances.
- The tell: the branches the model had already written out in its chat prose
  (Downtown, Airport Rd, Junction Sq's total, Hledan's growth) are exactly the
  ones that survived intact. Everything it had not said out loud got invented.
- Also: the chat's "Key Findings" table rendered as a header with zero rows.

### 2. Power BI deck — right numbers, invisible headings

- Every number on every slide matches the recorded query outputs. That is luck,
  not design — the same hand-typing mechanism as deck 1, it just transcribed
  correctly this time.
- The main heading of slides 2–5 is invisible (white text under the theme's white
  sheet — cause 3 above). The reader gets a deck of subtitles.
- "$6.70M across both campaigns" — it is $6.70M *each*, $13.4M combined; the
  slide's own invisible headline says "per campaign", so the deck contradicts
  itself and only the wrong version can be read.
- "$4.8B target", "regional accounts", "premium categories and bundles",
  inventory advice — none supported by any field in the dataset (quantity is
  empty on every row).
- "Top 10" in chat vs 5 rows on the slide; "2021 through 2024" in chat vs a 2025
  bar on the chart; a footer crediting "team analysis" instead of the real
  Power BI source. Data ends January 2025; the deck presents itself as a current
  2025 briefing in August 2026.

### 3. Fabric deck — the one the user flagged

Data:
- Monthly revenue chart: January–May are real 2023 values, June–December are
  invented; the invented months erase the real September and November dips and
  inflate the implied year by about 6%.
- "202.3M Thingyan peak" is April 2023 presented without a year (2025's April was
  187.3M). "170.1M Thadingyut surge (Oct–Dec)" is actually a single December
  figure from a third year, 2024.
- Every digital-channel and loyalty-tier total on slide 5 is wrong the same way:
  the model hand-summed a 16-row table and dropped one member-tier row from each
  sum. The slide's own headline (714M) and its three tier cards (674.5M) disagree
  by 40M on the same page.
- The training slide is 2023 data under the 2026 banner; "90%+ pass rate" is
  invented (real ≈52%); "7,182 hours Shared Knowledge Hub" is one unassigned row,
  not a program total; department "champions" are single rows presented as
  department totals.
- What was right: all five city cards, the basket-size champion, category
  revenues and own-brand shares — the numbers copied directly from single query
  rows are fine. It is the *derived* numbers (sums, rates, trends) that are wrong.

Visuals (the user's three complaints):
- **No heading**: cover title is white-on-white under the theme's sheet — 1.00:1
  contrast, invisible (cause 3). Same on the closing slide.
- **Light text**: a second, independent cause — the theme's "muted" grey
  (contrast ~3.1:1, below the 4.5:1 readability bar) is used for every subtitle,
  every card caption and every chart axis label on every slide. The deck is
  systemically too light even where the white-sheet bug does not fire.
- **Blur**: not in the deck. The PPTX is all vector and renders sharp. The
  preview images are made at one fixed 2000px size and get stretched on retina
  screens — the softness is in the preview pipeline, not the file. And the
  chat thumbnail is the broken white cover, compounding the "empty and washed
  out" impression.

### 4. Retail deck — mostly right, one dangerous invention

- The core comparison is exact everywhere: December 164.5M vs November 124.2M,
  +32.4%, all five banner totals, all top-5 and bottom-5 stores, baskets, units,
  average basket — database, code, slides and chat all agree.
- The one big invention: "City Rewards members contribute 78% of volume." Real
  share: 41%. The deck then aims an action item at walk-ins as a "22% of revenue"
  niche — walk-ins are actually 59%, the bigger half. The model computed the
  member/non-member *growth* correctly but never computed the *share*, and wrote
  a plausible-sounding loyalty ratio instead.
- Geography framing wrong: "steepest contraction in Mandalay" — 13 of the 16
  declining stores are in Yangon, only 2 in Upper Myanmar.
- "Last month" (asked in August 2026) silently became December 2025 — defensible,
  because that is the newest data in the database, but never disclosed.
- Visual: slide 5's two-line title collides with its subtitle; slide 3's axis
  labels render huge and unformatted.

---

## What would actually fix this (for decision, not done)

Listed for discussion only — nothing has been changed:

1. **Feed the slide code the real numbers.** The deck-builder function already
   receives the query results as a parameter — it just never reads them. Making
   the slides draw from that data (or checking every literal in the generated
   code against the recorded results before rendering) removes cause 1 entirely.
2. **Say when the data ends.** One line on the cover or footer — "data through
   Dec 2025" — and a hard rule that a zero-row query for the asked period must be
   disclosed, not silently substituted.
3. **Never paint the ground over a slide that set its own.** The theme's white
   sheet should either respect a slide background the model set, or re-color the
   text to match the ground it painted. Either alone kills the invisible-title bug.
4. **Teach the layout checker to read.** It already runs on every deck; adding a
   text-contrast check (title vs whatever is behind it) would have caught both
   broken decks while its six geometry flags were false alarms.
5. **Render previews at 2× for retina screens**, and pick a readable thumbnail.

---

## Where the evidence lives

- Rendered PDFs, extracted deck code, raw query outputs, unzipped slide XML and
  the verification scripts are under the session scratchpad
  (`/private/tmp/claude-501/-Users-rahulgupta/41114562-52a1-478c-9086-b44cb191017c/scratchpad/`,
  Fabric material under `fabricval/`).
- The four reports themselves are untouched and can be opened at
  `http://localhost:8095/reports/<id>` with the ids in the table above.
- Nothing in the repo, the containers or the database was modified during this
  validation.
