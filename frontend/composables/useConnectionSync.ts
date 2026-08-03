/**
 * Live sync state for a per-user Microsoft connector, shared across every
 * component that asks for it.
 *
 * Why a shared registry and not a ref per component
 * -------------------------------------------------
 * The same agent's state is rendered in several places at once — the agent row
 * in the rail, the agent page header, and the chat data-source picker. One
 * poller per component would mean three requests every two seconds for one
 * sync. Everything here is keyed on the data source id and reference-counted:
 * the first subscriber starts the poll, the last one to leave stops it.
 *
 * Why the poll rate changes
 * -------------------------
 * A sync that is running is worth watching closely; one that is not is worth
 * checking occasionally, because it may have been started somewhere else — in
 * the sign-in window, on another tab, or by a colleague's action on a shared
 * agent. So: fast while `syncing`, slow otherwise, and immediately on tab focus.
 * An idle agent costs one request every half minute, and only for the two
 * connectors this applies to.
 */
import { ref, computed, onMounted, onUnmounted, type Ref } from 'vue'
import { isPerUserConnector } from '~/utils/perUserConnector'

// ★One vocabulary, shared with every other progress tracker in the product —
// see backend/app/core/progress_status.py. This used to spell the same two
// outcomes 'done'/'error' while the indexing trackers spelled them
// 'completed'/'failed', so a check carried across from one screen to another
// was simply never true.
//
// ★`learning` is no longer a STATUS. It is still the stage a member most wants
// to watch — reading the workspaces is the fast half, writing the agent's
// overview is the ~30 seconds after — but "is it running" and "running what"
// are different questions, and it lives in `phase` where the other trackers
// keep theirs.
export type SyncStatus =
  | 'idle'
  | 'pending'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'

export interface SyncDetailRow {
  name: string
  kind?: string | null
  workspace?: string | null
  tenant?: string | null
  // ★Same vocabulary as the parent. This payload used to say 'done' at the
  // top and 'ok' per workspace — two spellings of one word, side by side.
  status: 'pending' | 'completed' | 'failed'
  tables: number
  error?: string | null
}

export interface SyncState {
  status: SyncStatus
  phase?: string | null
  endpoints_total: number
  endpoints_done: number
  endpoints_failed: number
  tables: number
  detail: SyncDetailRow[]
  error?: string | null
  /**
   * Which side failed. 'infrastructure' is ours — transient, retried
   * automatically, and not something the member can act on. 'source' is the
   * far side refusing us. null on rows written before the server classified,
   * which means exactly "no claim about cause" and must not be read as either.
   */
  error_kind?: 'infrastructure' | 'source' | 'unknown' | null
  elapsed_ms: number
  last_done_at?: string | null
}

const IDLE: SyncState = {
  status: 'idle',
  phase: null,
  endpoints_total: 0,
  endpoints_done: 0,
  endpoints_failed: 0,
  tables: 0,
  detail: [],
  error: null,
  error_kind: null,
  elapsed_ms: 0,
  last_done_at: null,
}

const FAST_MS = 2000
const SLOW_MS = 30000

interface Entry {
  state: Ref<SyncState>
  subscribers: number
  timer: any
  interval: number
  type: string
}

const registry = new Map<string, Entry>()

/** `fabric_user` posts to /fabric-signin/…, `powerbi_user` to /user-signin/…. */
function signinBase(type: string): string {
  return type === 'fabric_user' ? 'fabric-signin' : 'user-signin'
}

async function fetchOnce(dsId: string, entry: Entry): Promise<void> {
  try {
    const { data, error } = await useMyFetch(
      `/data_sources/${dsId}/${signinBase(entry.type)}/sync-status`,
      { method: 'GET' },
    )
    if (error.value) return          // transient; keep whatever we had
    const p = data.value as SyncState | null
    if (p) entry.state.value = { ...IDLE, ...p }
  } catch (e) {
    // Deliberately silent. A status poll that cannot reach the server must not
    // put an error on screen — the sync itself may be perfectly healthy, and
    // the next tick will correct us.
  }
}

/** Anything that is still working, as opposed to a settled result. */
export function isRunningStatus(status: SyncStatus): boolean {
  return status === 'running'
}

function desiredInterval(state: SyncState): number {
  // ★`learning` polls fast too. Missing it here would have been invisible in
  // testing — the strip would still appear, just updating every 30 seconds
  // during the exact stage the member is watching.
  return isRunningStatus(state.status) ? FAST_MS : SLOW_MS
}

function schedule(dsId: string, entry: Entry): void {
  if (entry.timer) clearInterval(entry.timer)
  entry.timer = setInterval(async () => {
    await fetchOnce(dsId, entry)
    // Re-arm if the cadence should change — going from slow to fast the moment
    // a sync appears is the whole point.
    const want = desiredInterval(entry.state.value)
    if (want !== entry.interval) {
      entry.interval = want
      schedule(dsId, entry)
    }
  }, entry.interval)
}

function onFocus(): void {
  // A member who comes back to the tab should not wait out a slow tick.
  registry.forEach((entry, dsId) => { fetchOnce(dsId, entry) })
}

let focusBound = false

export function useConnectionSync(dataSource: Ref<any> | any) {
  const ds = computed(() => (dataSource as any)?.value ?? dataSource)
  const dsId = computed<string | null>(() => ds.value?.id || null)
  const dsType = computed<string>(
    () => ds.value?.type || ds.value?.connection?.type || ds.value?.connections?.[0]?.type || '',
  )
  // Only the per-user Microsoft connectors have a sync to report. Everything
  // else gets a permanently idle state and never issues a request.
  const applies = computed(() => isPerUserConnector(dsType.value))

  const local = ref<SyncState>({ ...IDLE })

  function entryFor(): Entry | null {
    if (!applies.value || !dsId.value) return null
    let entry = registry.get(dsId.value)
    if (!entry) {
      entry = {
        state: ref<SyncState>({ ...IDLE }),
        subscribers: 0,
        timer: null,
        interval: SLOW_MS,
        type: dsType.value,
      }
      registry.set(dsId.value, entry)
    }
    return entry
  }

  // ★The entry is created HERE, during setup, and held — not looked up inside
  // the computed below.
  //
  // `registry` is a plain Map, which is not reactive. The first version created
  // the entry in onMounted and had `state` do `registry.get(id)` on every
  // evaluation. On its first run the Map was still empty, so the computed's
  // only tracked dependency was the fallback `local` ref; when onMounted later
  // inserted the entry, nothing invalidated the computed and it never
  // re-evaluated. The poll ran, the data arrived, and the UI showed idle
  // forever — no error, no warning, just a strip that never appeared.
  //
  // Resolving the entry once in setup means the computed's first evaluation
  // already reads `entry.state.value`, which IS a reactive ref, so every later
  // update propagates normally.
  const entry = entryFor()

  const state = computed<SyncState>(() => (entry ? entry.state.value : local.value))

  async function refresh(): Promise<void> {
    if (!entry || !dsId.value) return
    await fetchOnce(dsId.value, entry)
    const want = desiredInterval(entry.state.value)
    if (want !== entry.interval) {
      entry.interval = want
      schedule(dsId.value, entry)
    }
  }

  onMounted(async () => {
    if (!entry || !dsId.value) return
    entry.subscribers += 1
    if (entry.subscribers === 1) {
      await fetchOnce(dsId.value, entry)
      entry.interval = desiredInterval(entry.state.value)
      schedule(dsId.value, entry)
    }
    if (!focusBound && typeof window !== 'undefined') {
      window.addEventListener('focus', onFocus)
      focusBound = true
    }
  })

  onUnmounted(() => {
    if (!entry) return
    entry.subscribers -= 1
    if (entry.subscribers <= 0) {
      if (entry.timer) clearInterval(entry.timer)
      // The state object stays in the registry deliberately: a member who
      // navigates away and back sees the last known result immediately instead
      // of a blank strip that fills in a second later.
      entry.timer = null
      entry.subscribers = 0
    }
  })

  const isRunning = computed(() => isRunningStatus(state.value.status))
  // ★Reads `phase`, because learning is a stage of running, not a status.
  const isLearning = computed(() => state.value.phase === 'learning')
  const isPartial = computed(() => state.value.status === 'partial')
  const isError = computed(() => state.value.status === 'failed')
  const isDone = computed(() => state.value.status === 'completed')

  /** Fabric crawls workspaces; Power BI crawls tenants. Say which is true. */
  const unitPlural = computed(() => (dsType.value === 'fabric_user' ? 'workspaces' : 'tenants'))

  const percent = computed(() => {
    const s = state.value
    if (s.status === 'completed' || s.status === 'partial') return 100
    // Reading is finished by the time learning starts, so the reading bar is
    // full. The learning stage renders its own indeterminate bar.
    if (s.phase === 'learning') return 100
    if (s.status !== 'running') return 0
    if (s.phase === 'discovering') return 8
    if (!s.endpoints_total) return 12
    // Count everything SETTLED, failures included — a workspace that refused is
    // finished with, and excluding it stalls the bar on a sync that is moving.
    const settled = s.endpoints_done + s.endpoints_failed
    return Math.min(96, 12 + Math.round((settled / s.endpoints_total) * 84))
  })

  /**
   * Whether this is worth putting on screen at all.
   *
   * ★An idle connector that has never synced shows NOTHING. A status strip that
   * is always present is noise, and noise is what makes people stop reading the
   * one time it matters.
   */
  const hasSomethingToSay = computed(
    () => applies.value && (state.value.status !== 'idle' || !!state.value.last_done_at),
  )

  return {
    applies, state, refresh,
    isRunning, isLearning, isPartial, isError, isDone,
    unitPlural, percent, hasSomethingToSay,
  }
}
