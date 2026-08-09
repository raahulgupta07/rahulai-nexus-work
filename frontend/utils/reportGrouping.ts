/**
 * Partition the sidebar's recent-reports list into the time groups the nav
 * renders as headings.
 *
 * Pure logic on purpose: no Vue, no fetching, no imports. The caller owns the
 * list and re-runs this as live activity arrives.
 */

export type ReportGroupKey = 'pinned' | 'today' | 'yesterday' | 'prev7' | 'prev30' | 'older'

export interface ReportGroup {
    key: ReportGroupKey
    labelKey: string
    items: any[]
}

/**
 * Order is the render order, and it is also the match order: the first bucket a
 * report qualifies for wins, which is what makes `pinned` beat every age
 * bucket without a special case at the call site.
 */
const GROUPS: ReadonlyArray<{ key: ReportGroupKey; labelKey: string }> = [
    { key: 'pinned', labelKey: 'nav.groupPinned' },
    { key: 'today', labelKey: 'nav.groupToday' },
    { key: 'yesterday', labelKey: 'nav.groupYesterday' },
    { key: 'prev7', labelKey: 'nav.groupPrev7' },
    { key: 'prev30', labelKey: 'nav.groupPrev30' },
    { key: 'older', labelKey: 'nav.groupOlder' },
]

const DAY_MS = 24 * 60 * 60 * 1000

/** Matches an ISO date-time that carries no timezone designator. */
const NAKED_ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

/** The fractional-seconds part of an ISO timestamp, however many digits. */
const FRACTION = /\.(\d+)/

/**
 * Turn whatever the API handed us into epoch milliseconds, or `null` if it
 * cannot be read as a time at all.
 *
 * Two things about the server's format force this to be more than `new Date(s)`:
 *
 * 1. **No timezone suffix.** Rows come back as `2026-08-08T13:33:19.034930` and
 *    are UTC-naive — the backend stores UTC and serialises without an offset.
 *    ECMAScript says a date-*time* form with no offset is LOCAL time, so a plain
 *    `new Date()` on that string shifts every report by the viewer's UTC offset.
 *    In a UTC+X zone that drags this morning's reports into "Yesterday"; in
 *    UTC-X it pulls last night's into "Today". We therefore append `Z` when no
 *    offset is present. A string that DOES carry an offset (or a `Z`) is trusted
 *    as-is — that is a deliberate statement of zone, not an omission.
 *    Date-only strings (`2026-08-08`) are already UTC by spec, so they are left
 *    alone and stay consistent with the naked-datetime handling above.
 *
 * 2. **Microseconds.** The spec's grammar allows exactly three fraction digits;
 *    engines happen to tolerate six today, but that is not something to rely on
 *    for a value that decides what the user sees. Truncating to milliseconds is
 *    lossless for grouping — no bucket boundary is finer than a day.
 */
function toEpochMs(value: unknown): number | null {
    if (value instanceof Date) {
        const t = value.getTime()
        return Number.isNaN(t) ? null : t
    }
    if (typeof value === 'number') {
        return Number.isFinite(value) ? value : null
    }
    if (typeof value !== 'string') return null

    const raw = value.trim()
    if (!raw) return null

    // Space-separated datetimes appear in some payloads; the T form is what
    // every engine parses consistently.
    let normalized = raw.replace(' ', 'T')
    normalized = normalized.replace(FRACTION, (_m, digits: string) => '.' + digits.slice(0, 3).padEnd(3, '0'))
    if (NAKED_ISO.test(normalized)) normalized += 'Z'

    const parsed = Date.parse(normalized)
    return Number.isNaN(parsed) ? null : parsed
}

/**
 * The fallback chain. A report that was created and never run has no
 * `last_activity_at`, and one that was never edited has no `updated_at` — it
 * still has to appear in the sidebar, so we walk down to `created_at`.
 */
const TIMESTAMP_FIELDS = ['last_activity_at', 'updated_at', 'created_at'] as const

/**
 * The one instant a report is "at", in epoch ms, or `null` when nothing on the
 * row can be read as a time.
 *
 * Exported because the sidebar also renders a relative age per row (`2m`, `5h`,
 * `9d`), and that badge must read the SAME field through the SAME
 * normalization as the heading above it — otherwise a row sits under "Today"
 * and reads "9d". Both failure modes this guards against (the UTC-naive form
 * and the 6-digit fraction, see `toEpochMs`) are silent, so a second
 * implementation would not announce itself when it drifted.
 */
export function reportTime(report: Record<string, unknown>): number | null {
    for (const field of TIMESTAMP_FIELDS) {
        const t = toEpochMs(report[field])
        if (t !== null) return t
    }
    return null
}

/**
 * Midnight at the START of the day containing `now`, in the VIEWER'S local
 * zone. "Yesterday" is a calendar word: a report from 11pm last night is
 * yesterday's even though it is only a few hours old, so the boundaries are
 * local midnights rather than rolling 24-hour windows.
 *
 * Works on a copy — the caller's `now` is never mutated.
 */
function startOfLocalDay(now: Date): number {
    const d = new Date(now.getTime())
    d.setHours(0, 0, 0, 0)
    return d.getTime()
}

function bucketFor(report: Record<string, unknown>, startOfToday: number): ReportGroupKey {
    if (report.is_starred === true) return 'pinned'

    const t = reportTime(report)
    // Unreadable or entirely absent timestamps land in `older` rather than
    // being dropped: a row missing from the sidebar looks like data loss, a row
    // filed at the bottom looks like an old report.
    if (t === null) return 'older'

    if (t >= startOfToday) return 'today'
    if (t >= startOfToday - DAY_MS) return 'yesterday'
    if (t >= startOfToday - 7 * DAY_MS) return 'prev7'
    if (t >= startOfToday - 30 * DAY_MS) return 'prev30'
    return 'older'
}

/**
 * Partition — never reorder. The backend already sorts `is_starred DESC,
 * last_activity_at DESC` and the caller re-sorts as runs emit activity, so
 * items keep their arrival order within each group.
 *
 * Empty groups are omitted: a heading with nothing under it reads as a loading
 * failure, so a brand-new workspace shows exactly one group.
 */
export function groupReports(reports: any[], now?: Date): ReportGroup[] {
    if (!Array.isArray(reports) || reports.length === 0) return []

    const reference = now instanceof Date && !Number.isNaN(now.getTime()) ? now : new Date()
    const startOfToday = startOfLocalDay(reference)

    const buckets = new Map<ReportGroupKey, any[]>()
    for (const report of reports) {
        if (!report || typeof report !== 'object') continue
        const key = bucketFor(report as Record<string, unknown>, startOfToday)
        const items = buckets.get(key)
        if (items) items.push(report)
        else buckets.set(key, [report])
    }

    const out: ReportGroup[] = []
    for (const group of GROUPS) {
        const items = buckets.get(group.key)
        if (items && items.length > 0) out.push({ key: group.key, labelKey: group.labelKey, items })
    }
    return out
}
