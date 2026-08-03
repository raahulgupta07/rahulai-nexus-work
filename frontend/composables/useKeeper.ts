// The sync-history feed behind the Keeper button.
//
// One poll for the whole app, not one per caller. `useConnectionSync` keeps a
// registry keyed by data source because it polls a per-connector endpoint;
// `GET /api/keeper` is already the answer for every agent the member can see,
// so a second subscriber must reuse the first one's timer rather than double the
// request rate.
//
// ★Cadence follows the data, as it does in useConnectionSync: fast while
// something is actually running, slow otherwise. A button that says "working"
// but updates every minute is worse than no button — the member watches a
// spinner that has been finished for fifty seconds.

export type KeeperResult = 'completed' | 'partial' | 'failed' | 'cancelled' | 'running'

export type KeeperRun = {
  id: string
  data_source_id: string | null
  data_source_name: string
  result: KeeperResult
  trigger: string | null
  phase: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  tables: number
  workspaces_total: number
  workspaces_done: number
  workspaces_failed: number
  error: string | null
  error_kind: string | null
  abandoned: boolean
}

export type KeeperAgent = {
  data_source_id: string
  name: string
  last_run: KeeperRun | null
  last_success_at: string | null
  runs: KeeperRun[]
  never_synced: boolean
}

export type KeeperProblem = {
  kind: string
  data_source_id: string
  data_source_name: string
  run_id: string
  detail: string
  since: string | null
}

export type KeeperOverview = {
  working_now: KeeperRun[]
  today: { runs: number; completed: number; failed: number; tables: number }
  agents: KeeperAgent[]
  needs_a_person: KeeperProblem[]
  recent: KeeperRun[]
}

/** What the button is showing. `hidden` is a real state, not an absence — a
 *  member with no agents at all should see no button rather than an empty one. */
export type KeeperState = 'hidden' | 'working' | 'attention' | 'resting'

const EMPTY: KeeperOverview = {
  working_now: [],
  today: { runs: 0, completed: 0, failed: 0, tables: 0 },
  agents: [],
  needs_a_person: [],
  recent: [],
}

const FAST_MS = 5000
const SLOW_MS = 60000

// Module scope: shared by every component that calls the composable.
const overview = ref<KeeperOverview | null>(null)
const loaded = ref(false)
let subscribers = 0
let timer: any = null
let interval = SLOW_MS
let focusBound = false
let inFlight = false

async function fetchOnce(): Promise<void> {
  // A slow response must not queue a second request behind it; at FAST_MS that
  // is how a struggling server gets a pile-on from the one screen watching it.
  if (inFlight) return
  inFlight = true
  try {
    const { data, error } = await useMyFetch('/keeper', { method: 'GET' })
    if (error.value) return          // transient — keep what we had
    if (data.value) {
      overview.value = { ...EMPTY, ...(data.value as KeeperOverview) }
      loaded.value = true
    }
  } catch (e) {
    // Deliberately silent, for the same reason useConnectionSync is: a failed
    // status poll says nothing about whether the syncs themselves are healthy,
    // and a toast on every tick would be unusable.
  } finally {
    inFlight = false
  }
}

function desiredInterval(): number {
  return (overview.value?.working_now?.length || 0) > 0 ? FAST_MS : SLOW_MS
}

function schedule(): void {
  if (timer) clearInterval(timer)
  timer = setInterval(async () => {
    await fetchOnce()
    const want = desiredInterval()
    if (want !== interval) {
      interval = want
      schedule()
    }
  }, interval)
}

function onFocus(): void {
  fetchOnce()
}

export type KeeperActivity = { items: KeeperRun[]; total: number; limit: number; offset: number }

export type KeeperWorkspace = {
  name?: string
  status?: string
  tables?: number
  error?: string
  [key: string]: any
}

export type KeeperRunDetail = KeeperRun & {
  workspaces: KeeperWorkspace[]
  events: any[]
}

export type KeeperScheduleAgent = {
  data_source_id: string
  name: string
  runs_when: 'signin' | 'auto_learn'
}

export type KeeperSchedule = {
  auto_learn: {
    enabled: boolean
    quiet_minutes: number
    max_runs_per_day: number
    runs_today: number
    sweep_every_minutes: number
  }
  agents: KeeperScheduleAgent[]
  per_user_count: number
}

// ★These three are NOT polled and NOT cached at module scope, unlike the
// overview. They are read when a tab is opened and when the member asks again.
// The overview is a heartbeat; a paginated list that reshuffles under a reader's
// cursor every five seconds is hostile.
export async function fetchKeeperActivity(params: {
  data_source_id?: string | null
  problems_only?: boolean
  days?: number | null
  limit?: number
  offset?: number
} = {}): Promise<KeeperActivity> {
  const q = new URLSearchParams()
  if (params.data_source_id) q.set('data_source_id', params.data_source_id)
  if (params.problems_only) q.set('problems_only', 'true')
  if (params.days) q.set('days', String(params.days))
  q.set('limit', String(params.limit ?? 50))
  q.set('offset', String(params.offset ?? 0))
  const { data } = await useMyFetch(`/keeper/activity?${q.toString()}`, { method: 'GET' })
  return (data.value as KeeperActivity) || { items: [], total: 0, limit: 50, offset: 0 }
}

/** Null when the run does not exist, is not this member's, or sits on an agent
 *  they cannot see — the backend deliberately answers all three the same way. */
export async function fetchKeeperRun(runId: string): Promise<KeeperRunDetail | null> {
  const { data, error } = await useMyFetch(`/keeper/runs/${runId}`, { method: 'GET' })
  if (error.value) return null
  return (data.value as KeeperRunDetail) || null
}

export type KeeperSyncAll = {
  queued: { data_source_id: string; name: string }[]
  skipped: { data_source_id: string; name: string; reason: string }[]
}

/** Queue a sync for every agent the member can sync. The syncs run one at a
 *  time on the server — see `app/services/keeper_actions.py` for why. */
export async function keeperSyncAll(): Promise<KeeperSyncAll | null> {
  const { data, error } = await useMyFetch('/keeper/sync-all', { method: 'POST' })
  if (error.value) return null
  return (data.value as KeeperSyncAll) || null
}

export async function fetchKeeperSchedule(): Promise<KeeperSchedule | null> {
  const { data, error } = await useMyFetch('/keeper/schedule', { method: 'GET' })
  if (error.value) return null
  return (data.value as KeeperSchedule) || null
}

export function useKeeper() {
  const data = computed<KeeperOverview>(() => overview.value || EMPTY)

  const workingCount = computed(() => data.value.working_now.length)
  const problemCount = computed(() => data.value.needs_a_person.length)

  /** ★The whole point of the button: this is derived, never set by a caller.
   *  Two screens cannot disagree about whether a sync is running. */
  const state = computed<KeeperState>(() => {
    if (!loaded.value) return 'hidden'
    if (workingCount.value > 0) return 'working'
    if (problemCount.value > 0) return 'attention'
    // Nothing to keep. A member whose organization has no agents they can see
    // has nothing this button could tell them.
    if (data.value.agents.length === 0) return 'hidden'
    return 'resting'
  })

  /** Newest last-run timestamp across every agent — "last checked" for the
   *  resting state, which is the one fact that makes resting believable. */
  const lastActivityAt = computed<string | null>(() => {
    // ★Compared as instants, not as strings. These are `datetime.isoformat()`,
    // which drops the microseconds when they happen to be zero — so two
    // timestamps a millisecond apart can differ in LENGTH, and a lexicographic
    // max is then deciding on punctuation. It would be right almost always,
    // which is the worst kind of wrong to debug.
    let best: string | null = null
    let bestMs = -Infinity
    for (const a of data.value.agents) {
      const at = a.last_run?.finished_at || a.last_run?.started_at || null
      if (!at) continue
      // Naive UTC from the API: `Date.parse` would read it as local time. The
      // absolute value is never displayed from here — `relativeTime` re-parses
      // it properly — so a consistent misreading still picks the right row.
      const ms = Date.parse(at)
      if (!isNaN(ms) && ms > bestMs) { bestMs = ms; best = at }
    }
    return best
  })

  const refresh = () => fetchOnce()

  onMounted(() => {
    subscribers += 1
    if (subscribers === 1) {
      fetchOnce()
      interval = SLOW_MS
      schedule()
      if (!focusBound && typeof window !== 'undefined') {
        window.addEventListener('focus', onFocus)
        focusBound = true
      }
    }
  })

  onUnmounted(() => {
    subscribers = Math.max(0, subscribers - 1)
    if (subscribers === 0 && timer) {
      clearInterval(timer)
      timer = null
    }
  })

  return { data, loaded, state, workingCount, problemCount, lastActivityAt, refresh }
}
