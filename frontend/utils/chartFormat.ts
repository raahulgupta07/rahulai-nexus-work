/**
 * How a chart writes numbers and category labels — one definition.
 *
 * The same two rules live in `backend/app/services/number_format.py`, which is
 * what the Word and PowerPoint export paths use. Keeping the algorithms
 * identical is the point: before this, each render path re-invented axis
 * formatting and the exports printed `70000000000` for an axis the browser
 * showed as `4.3B`.
 *
 * Neither function knows anything about any dataset, column or unit.
 */

// Magnitude → suffix, largest first. Mirrors ABBREVIATION_STEPS in
// backend/app/services/number_format.py.
const ABBREVIATION_STEPS: Array<[number, string]> = [
  [1e12, 'T'],
  [1e9, 'B'],
  [1e6, 'M'],
  [1e3, 'K'],
]

/** Write a value the way every value axis in the product writes it. */
export function abbreviateAxisValue(value: any, decimals = 1): string {
  const n = Number(value)
  if (value == null || Number.isNaN(n)) return String(value ?? '')
  const magnitude = Math.abs(n)
  for (const [threshold, suffix] of ABBREVIATION_STEPS) {
    if (magnitude >= threshold) return (n / threshold).toFixed(decimals) + suffix
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

/** ECharts `axisLabel.formatter` for any value axis. */
export const valueAxisLabelFormatter = (v: any) => abbreviateAxisValue(v)

/**
 * Find a column that tells apart rows sharing one category label.
 *
 * Returns the first column giving two different values to rows with the same
 * category — the column that proves the label means more than one thing — or
 * null when the label is unambiguous. Purely numeric columns are skipped: a
 * number is a measure, not an identity.
 */
export function pickQualifierColumn(
  rows: any[],
  categoryKey: string,
  excluded: Array<string | null | undefined> = [],
): string | null {
  if (!rows || !rows.length) return null
  const ex = new Set(excluded.filter(Boolean) as string[])
  for (const key of Object.keys(rows[0] || {})) {
    if (key === categoryKey || ex.has(key)) continue
    if (rows.every(r => typeof r[key] === 'number')) continue
    const seen = new Map<string, string>()
    let splits = false
    for (const r of rows) {
      const cat = String(r[categoryKey] ?? '')
      const val = String(r[key] ?? '')
      if (seen.has(cat) && seen.get(cat) !== val) { splits = true; break }
      seen.set(cat, val)
    }
    if (splits) return key
  }
  return null
}

/**
 * Make every label on a category axis identify exactly one thing.
 *
 * Two rows can carry the same label and mean different things — the same leaf
 * name under two parents of a hierarchy, say. Drawing both under one identical
 * string presents two different things as one. Where a qualifier is available
 * the label becomes `Common (ParentA)`; where it is not, the duplicates are
 * numbered `Common (1 of 2)` so the ambiguity is stated rather than hidden.
 * Already-unique labels come back untouched.
 */
export function qualifyDuplicateLabels(
  labels: any[],
  qualifiers?: any[] | null,
): string[] {
  const raw = labels.map(l => (l == null ? '' : String(l)))
  const quals =
    qualifiers && qualifiers.length === raw.length
      ? qualifiers.map(q => (q == null ? '' : String(q)))
      : null

  const counts = new Map<string, number>()
  raw.forEach(l => counts.set(l, (counts.get(l) || 0) + 1))

  const seen = new Map<string, number>()
  const used = new Set<string>()
  return raw.map((label, i) => {
    if ((counts.get(label) || 0) < 2) {
      used.add(label)
      return label
    }
    const nth = (seen.get(label) || 0) + 1
    seen.set(label, nth)
    const qualifier = quals ? quals[i].trim() : ''
    let candidate =
      qualifier && qualifier !== label
        ? `${label} (${qualifier})`
        : `${label} (${nth} of ${counts.get(label)})`
    // A qualifier can itself repeat; never hand back a label twice.
    if (used.has(candidate)) candidate = `${candidate} [${nth}]`
    used.add(candidate)
    return candidate
  })
}
