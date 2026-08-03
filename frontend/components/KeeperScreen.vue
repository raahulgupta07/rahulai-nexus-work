<template>
  <!-- ★The same shell as AllInstructionsModal — deliberately, not incidentally.
       This started life as a `fixed inset-0` overlay, which took the whole
       window and REPLACED the Agents screen rather than sitting over it. Nothing
       then signalled you were inside a temporary view, and the only way back was
       one button. Every other cross-cutting view here (All instructions,
       Connections, Trace) is a centered card over a dimmed page, so this is too.

       ★`UModal` owns Escape, focus trapping and the backdrop click. The overlay
       version hand-rolled all three; keeping them would mean two things racing
       to close the same dialog. -->
  <UModal
    :model-value="isOpen"
    :ui="{ width: 'sm:max-w-5xl' }"
    @update:model-value="v => { if (!v) close() }"
  >
    <UCard :ui="{ body: { padding: '' }, header: { padding: 'px-4 pt-3 pb-0' } }">
      <template #header>
        <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 class="text-base font-semibold text-gray-900 dark:text-white">{{ $t('keeper.screenTitle') }}</h3>
          <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.screenSubtitle') }}</span>
          <div class="flex-1"></div>
          <UButton
            color="gray" variant="ghost" size="xs"
            icon="i-heroicons-x-mark-20-solid"
            :aria-label="$t('keeper.close')"
            @click="close()"
          />
        </div>

        <!-- Tabs -->
        <nav class="-mb-px mt-3 flex gap-5" role="tablist">
          <button
            v-for="tab in TABS"
            :key="tab"
            type="button"
            role="tab"
            :aria-selected="activeTab === tab"
            class="flex items-center gap-1.5 whitespace-nowrap border-b-2 pb-2 text-[13px]"
            :class="activeTab === tab
              ? 'border-indigo-500 font-medium text-gray-900 dark:text-white'
              : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'"
            @click="goTo(tab)"
          >
            {{ $t(`keeper.tab.${tab}`) }}
            <!-- The count rides on the tab so a member does not have to open it to
                 learn there is nothing there. -->
            <span
              v-if="tab === 'needs' && problemCount"
              class="rounded-full bg-amber-100 px-1.5 py-px font-mono text-[10px] tabular-nums text-amber-700 dark:bg-amber-500/20 dark:text-amber-400"
            >{{ problemCount }}</span>
          </button>
        </nav>
      </template>

      <!-- ★A fixed height, like AllInstructionsModal's 62vh. A card that grows
           with its content makes the tab strip jump every time you switch tab,
           and the Schedule tab is a third the height of Activity. -->
      <div class="overflow-y-auto px-4 py-4 text-sm" style="height: 62vh; min-height: 380px;">
        <!-- ───────────────────────── Activity ───────────────────────── -->
        <div v-if="activeTab === 'activity'">
          <div class="mb-3 flex flex-wrap items-center gap-2">
            <select
              :value="filterAgent"
              class="h-8 rounded-lg border border-gray-200 bg-white px-2 text-xs dark:border-gray-800 dark:bg-gray-900"
              @change="setAgentFilter(($event.target as HTMLSelectElement).value)"
            >
              <option value="">{{ $t('keeper.allAgents') }}</option>
              <option v-for="a in data.agents" :key="a.data_source_id" :value="a.data_source_id">{{ a.name }}</option>
            </select>
            <label class="inline-flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400">
              <input v-model="problemsOnly" type="checkbox" class="h-3.5 w-3.5" @change="loadActivity()" />
              {{ $t('keeper.problemsOnly') }}
            </label>
            <button
              type="button"
              class="ms-auto inline-flex h-8 items-center gap-1.5 rounded-lg border border-gray-200 px-2.5 text-xs text-gray-600 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-400 dark:hover:bg-gray-800/50"
              @click="loadActivity()"
            >
              <UIcon name="i-heroicons-arrow-path" class="h-3.5 w-3.5" :class="activityLoading ? 'animate-spin' : ''" />
              {{ $t('keeper.refresh') }}
            </button>
            <button
              type="button"
              class="inline-flex h-8 items-center gap-1.5 rounded-lg bg-blue-600 px-2.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              :disabled="syncingAll"
              @click="syncAll()"
            >
              <UIcon name="i-heroicons-bolt" class="h-3.5 w-3.5" />
              {{ $t('keeper.syncAll') }}
            </button>
          </div>

          <!-- What the button actually did. ★"Queued 2 of 5" with no reason is
               the shape that gets reported as data loss; every agent that was
               passed over says why it was. -->
          <div v-if="syncAllResult" class="mb-3 rounded-lg border border-gray-200 p-2.5 text-[11px] dark:border-gray-800">
            <p class="text-gray-700 dark:text-gray-300">
              {{ syncAllResult.queued.length
                ? $t('keeper.queuedN', { n: syncAllResult.queued.length })
                : $t('keeper.queuedNone') }}
            </p>
            <p v-for="s in syncAllResult.skipped" :key="s.data_source_id" class="mt-0.5 text-gray-500 dark:text-gray-400">
              {{ s.name }} — {{ $t(`keeper.skipped.${s.reason}`) }}
            </p>
          </div>

          <p v-if="!activityLoading && !activity.items.length" class="py-8 text-center text-xs text-gray-500 dark:text-gray-400">
            {{ problemsOnly ? $t('keeper.noProblems') : $t('keeper.noRuns') }}
          </p>

          <div v-for="run in activity.items" :key="run.id" class="border-b border-gray-100 last:border-0 dark:border-gray-800/60">
            <!-- ★Two lines, not seven columns on one.
                 The row carries agent · result · trigger · duration · time. At
                 the 1024px this modal shares with All instructions those crowd,
                 and the first casualties are the trigger chip and the duration —
                 the two that answer "did I start this, and how long did it take".
                 So the outcome stays on line one, where it is scanned, and the
                 circumstances drop to a muted line beneath. -->
            <button
              type="button"
              class="w-full py-2 text-start hover:bg-gray-50 dark:hover:bg-gray-900/50"
              :aria-expanded="openRunId === run.id"
              @click="toggleRun(run.id)"
            >
              <span class="flex items-center gap-2.5">
                <UIcon
                  name="i-heroicons-chevron-right"
                  class="h-3.5 w-3.5 shrink-0 text-gray-400 transition-transform"
                  :class="openRunId === run.id ? 'rotate-90' : ''"
                />
                <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="dotClass(run.result)"></span>
                <span class="min-w-0 flex-1 truncate font-medium text-gray-800 dark:text-gray-200">{{ run.data_source_name }}</span>
                <span class="shrink-0 text-[11px]" :class="resultTextClass(run.result)">{{ $t(`keeper.result.${run.result}`) }}</span>
              </span>
              <span class="mt-0.5 flex items-center gap-2 ps-6 text-[11px] text-gray-400 dark:text-gray-500">
                <span v-if="run.trigger" class="rounded bg-gray-100 px-1.5 py-px text-[10px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                  {{ $t(`keeper.trigger.${run.trigger}`) }}
                </span>
                <span v-if="run.duration_ms != null" class="font-mono tabular-nums">
                  {{ humanDuration(run.duration_ms) }}
                </span>
                <span v-if="run.started_at">{{ relativeTime(run.started_at) }}</span>
                <!-- A partial result's headline number belongs here too: "3 of 4"
                     is the reason somebody opens the row at all. -->
                <span v-if="run.workspaces_failed" class="text-amber-600 dark:text-amber-400">
                  {{ run.workspaces_done }}/{{ run.workspaces_total }}
                </span>
              </span>
            </button>

            <!-- Expanded detail. Fetched on open, not with the list — the
                 per-workspace breakdown and event log are the large half of the
                 payload and most rows are never opened. -->
            <div v-if="openRunId === run.id" class="pb-3 ps-8 pe-2">
              <p v-if="detailLoading" class="py-2 text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.loading') }}</p>
              <p v-else-if="!detail" class="py-2 text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.runGone') }}</p>
              <template v-else>
                <p v-if="detail.error" class="mb-2 rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-snug text-red-700 dark:bg-red-500/10 dark:text-red-400">
                  {{ detail.error }}
                  <span v-if="detail.error_kind === 'infrastructure'" class="block text-red-600/80 dark:text-red-400/70">
                    {{ $t('keeper.ourSide') }}
                  </span>
                </p>

                <div v-if="detail.workspaces.length" class="mb-2">
                  <p class="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    {{ $t('keeper.workspaces') }}
                  </p>
                  <div v-for="(ws, i) in detail.workspaces" :key="i" class="flex items-center gap-2 py-0.5 text-[11px]">
                    <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="ws.status === 'failed' ? 'bg-red-500' : 'bg-green-500'"></span>
                    <span class="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{{ ws.name || $t('keeper.unnamedWorkspace') }}</span>
                    <span v-if="ws.tables != null" class="shrink-0 font-mono tabular-nums text-gray-400 dark:text-gray-500">{{ ws.tables }}</span>
                    <span v-if="ws.error" class="max-w-[50%] shrink-0 truncate text-red-600 dark:text-red-400">{{ ws.error }}</span>
                  </div>
                </div>

                <div v-if="detail.events.length">
                  <p class="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    {{ $t('keeper.events') }}
                  </p>
                  <div class="max-h-48 overflow-y-auto rounded-md bg-gray-50 p-2 dark:bg-gray-900">
                    <p v-for="(ev, i) in detail.events" :key="i" class="font-mono text-[10px] leading-relaxed text-gray-600 dark:text-gray-400">
                      {{ eventLine(ev) }}
                    </p>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!-- `items` accumulates across pages, so it IS the count loaded so
               far — adding an offset on top double-counts and hides the button
               halfway through the list. -->
          <div v-if="activity.total > activity.items.length" class="pt-3 text-center">
            <button
              type="button"
              class="rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-400 dark:hover:bg-gray-800/50"
              @click="loadMore()"
            >
              {{ $t('keeper.loadMore') }}
            </button>
          </div>
        </div>

        <!-- ───────────────────────── Overview ───────────────────────── -->
        <div v-else-if="activeTab === 'overview'">
          <div class="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div v-for="card in overviewCards" :key="card.key" class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
              <p class="font-mono text-xl tabular-nums" :class="card.tone">{{ card.value }}</p>
              <p class="text-[11px] text-gray-500 dark:text-gray-400">{{ card.label }}</p>
            </div>
          </div>

          <div v-if="data.working_now.length" class="mb-4">
            <p class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('keeper.workingNow') }}</p>
            <div v-for="run in data.working_now" :key="run.id" class="flex items-center gap-2 py-1 text-xs">
              <UIcon name="i-heroicons-arrow-path" class="h-3.5 w-3.5 shrink-0 animate-spin text-blue-500" />
              <span class="min-w-0 flex-1 truncate text-gray-800 dark:text-gray-200">{{ run.data_source_name }}</span>
              <span v-if="run.phase" class="shrink-0 text-gray-500 dark:text-gray-400">{{ run.phase }}</span>
              <span v-if="run.workspaces_total" class="shrink-0 font-mono tabular-nums text-gray-400 dark:text-gray-500">
                {{ run.workspaces_done }}/{{ run.workspaces_total }}
              </span>
            </div>
          </div>

          <p class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('keeper.recent') }}</p>
          <p v-if="!data.recent.length" class="text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.noRuns') }}</p>
          <button
            v-for="run in data.recent"
            :key="run.id"
            type="button"
            class="flex w-full items-center gap-2 py-1 text-start text-xs hover:bg-gray-50 dark:hover:bg-gray-900/50"
            @click="openRunFromAnywhere(run.id)"
          >
            <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="dotClass(run.result)"></span>
            <span class="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{{ run.data_source_name }}</span>
            <span class="shrink-0 text-[11px] text-gray-400 dark:text-gray-500">{{ run.started_at ? relativeTime(run.started_at) : '' }}</span>
          </button>
        </div>

        <!-- ────────────────────────── Agents ────────────────────────── -->
        <div v-else-if="activeTab === 'agents'">
          <p v-if="!data.agents.length" class="py-8 text-center text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.noAgents') }}</p>
          <div
            v-for="agent in data.agents"
            :key="agent.data_source_id"
            class="flex items-center gap-3 border-b border-gray-100 py-2.5 last:border-0 dark:border-gray-800/60"
          >
            <div class="min-w-0 flex-1">
              <p class="truncate font-medium text-gray-800 dark:text-gray-200">{{ agent.name }}</p>
              <p class="text-[11px] text-gray-500 dark:text-gray-400">
                <template v-if="agent.never_synced">{{ $t('keeper.neverSynced') }}</template>
                <template v-else-if="agent.last_success_at">{{ $t('keeper.lastSuccess', { when: relativeTime(agent.last_success_at) }) }}</template>
                <template v-else>{{ $t('keeper.noSuccessYet') }}</template>
              </p>
            </div>
            <!-- Last seven runs, oldest on the left. Reading the shape of a
                 failure run is the point; a single "last result" chip cannot
                 tell "broken once" from "broken all week". -->
            <div class="flex shrink-0 items-center gap-1" :title="$t('keeper.sparkTitle')">
              <span
                v-for="run in [...agent.runs].reverse()"
                :key="run.id"
                class="h-4 w-1.5 rounded-sm"
                :class="dotClass(run.result)"
              ></span>
            </div>
            <button
              type="button"
              class="shrink-0 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-400 dark:hover:bg-gray-800/50"
              @click="showAgentActivity(agent.data_source_id)"
            >
              {{ $t('keeper.viewRuns') }}
            </button>
          </div>
        </div>

        <!-- ────────────────────── Needs a person ────────────────────── -->
        <div v-else-if="activeTab === 'needs'">
          <div v-if="!data.needs_a_person.length" class="py-8 text-center">
            <UIcon name="i-heroicons-check-circle" class="mx-auto mb-2 h-6 w-6 text-green-500" />
            <p class="text-xs text-gray-600 dark:text-gray-400">{{ $t('keeper.nothingNeedsYou') }}</p>
            <p class="mt-1 text-[11px] text-gray-400 dark:text-gray-500">{{ $t('keeper.ourOutagesExcluded') }}</p>
          </div>
          <div
            v-for="problem in data.needs_a_person"
            :key="problem.run_id + problem.kind"
            class="mb-2 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10"
          >
            <div class="flex items-start gap-2">
              <UIcon name="i-heroicons-exclamation-triangle" class="mt-px h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <div class="min-w-0 flex-1">
                <p class="font-medium text-gray-900 dark:text-gray-100">{{ problem.data_source_name }}</p>
                <p class="mt-0.5 text-[11px] leading-snug text-gray-700 dark:text-gray-300">{{ problem.detail }}</p>
                <p v-if="problem.since" class="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
                  {{ $t('keeper.since', { when: relativeTime(problem.since) }) }}
                </p>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-md border border-amber-300 px-2 py-1 text-[11px] text-amber-800 hover:bg-amber-100 dark:border-amber-500/40 dark:text-amber-300 dark:hover:bg-amber-500/20"
                @click="openRunFromAnywhere(problem.run_id)"
              >
                {{ $t('keeper.openRun') }}
              </button>
            </div>
          </div>
        </div>

        <!-- ───────────────────────── Schedule ───────────────────────── -->
        <div v-else-if="activeTab === 'schedule'">
          <p v-if="!schedule" class="py-8 text-center text-xs text-gray-500 dark:text-gray-400">{{ $t('keeper.loading') }}</p>
          <template v-else>
            <!-- ★The sentence this whole tab exists for. -->
            <div v-if="schedule.per_user_count" class="mb-4 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
              <p class="font-medium text-gray-800 dark:text-gray-200">{{ $t('keeper.signinTitle') }}</p>
              <p class="mt-1 text-[11px] leading-snug text-gray-600 dark:text-gray-400">
                {{ $t('keeper.signinExplain', { n: schedule.per_user_count }) }}
              </p>
            </div>

            <div class="mb-4 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
              <div class="flex items-center gap-2">
                <p class="font-medium text-gray-800 dark:text-gray-200">{{ $t('keeper.autoLearn') }}</p>
                <span
                  class="rounded px-1.5 py-px text-[10px]"
                  :class="schedule.auto_learn.enabled
                    ? 'bg-green-100 text-green-700 dark:bg-green-500/20 dark:text-green-400'
                    : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'"
                >{{ schedule.auto_learn.enabled ? $t('keeper.on') : $t('keeper.off') }}</span>
              </div>
              <p v-if="schedule.auto_learn.enabled" class="mt-1 text-[11px] leading-snug text-gray-600 dark:text-gray-400">
                {{ $t('keeper.autoLearnExplain', {
                  every: schedule.auto_learn.sweep_every_minutes,
                  quiet: schedule.auto_learn.quiet_minutes,
                }) }}
              </p>
              <p v-else class="mt-1 text-[11px] text-gray-600 dark:text-gray-400">{{ $t('keeper.autoLearnOff') }}</p>
              <p v-if="schedule.auto_learn.enabled" class="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                {{ $t('keeper.autoLearnBudget', {
                  spent: schedule.auto_learn.runs_today,
                  max: schedule.auto_learn.max_runs_per_day,
                }) }}
              </p>
            </div>

            <p class="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('keeper.perAgent') }}</p>
            <div
              v-for="agent in schedule.agents"
              :key="agent.data_source_id"
              class="flex items-center gap-3 border-b border-gray-100 py-2 last:border-0 dark:border-gray-800/60"
            >
              <span class="min-w-0 flex-1 truncate text-gray-800 dark:text-gray-200">{{ agent.name }}</span>
              <span class="shrink-0 text-[11px] text-gray-500 dark:text-gray-400">
                {{ $t(`keeper.runsWhen.${agent.runs_when}`) }}
              </span>
            </div>
          </template>
        </div>
      </div>
    </UCard>
  </UModal>
</template>

<script setup lang="ts">
// The full sync-history screen.
//
// ★The URL is the state, not a ref in here. `?keeper=<tab>&run=<id>` decides
// whether the screen is open, which tab shows, and which run is expanded — so
// back, forward, refresh and a pasted link all behave, and there is no second
// copy of "is it open" to fall out of step with the first. Closing means
// removing the query, which is why `close()` navigates rather than setting a
// flag.
import {
  fetchKeeperActivity, fetchKeeperRun, fetchKeeperSchedule, keeperSyncAll,
  type KeeperActivity, type KeeperRunDetail, type KeeperSchedule, type KeeperSyncAll,
} from '~/composables/useKeeper'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { relativeTime } = useRelativeTime()
const { data, problemCount, refresh } = useKeeper()

const TABS = ['activity', 'overview', 'agents', 'needs', 'schedule'] as const
type Tab = typeof TABS[number]

const isOpen = computed(() => TABS.includes(route.query.keeper as Tab))
// Activity is the landing tab: "what just happened" is the question people
// arrive with. Overview is a summary of it, which is only worth reading second.
const activeTab = computed<Tab>(() => (route.query.keeper as Tab) || 'activity')
const openRunId = computed<string | null>(() => (route.query.run as string) || null)

const activity = ref<KeeperActivity>({ items: [], total: 0, limit: 50, offset: 0 })
const activityLoading = ref(false)
const detail = ref<KeeperRunDetail | null>(null)
const detailLoading = ref(false)
const schedule = ref<KeeperSchedule | null>(null)
// ★In the URL, like the tab and the open run. The sync strip on an agent page
// links straight to that agent's history, and "View syncs" on the Agents tab
// does the same jump — both would be one-way trips if the filter lived only in
// a ref, because the back button would return to a screen showing every agent.
const filterAgent = computed(() => (route.query.agent as string) || '')
const problemsOnly = ref(false)
const syncingAll = ref(false)
const syncAllResult = ref<KeeperSyncAll | null>(null)

function setQuery(patch: Record<string, string | undefined>) {
  const query: Record<string, any> = { ...route.query, ...patch }
  Object.keys(query).forEach(k => { if (query[k] === undefined) delete query[k] })
  router.push({ query })
}

function close() { setQuery({ keeper: undefined, run: undefined }) }
function goTo(tab: Tab) { setQuery({ keeper: tab, run: undefined }) }

function toggleRun(id: string) {
  setQuery({ run: openRunId.value === id ? undefined : id })
}

/** Open a run from a tab that is not Activity: switch tab AND expand, in one
 *  navigation, so the back button undoes the whole jump rather than half of it. */
function openRunFromAnywhere(id: string) { setQuery({ keeper: 'activity', run: id }) }

function setAgentFilter(dsId: string) {
  setQuery({ agent: dsId || undefined, run: undefined })
}

function showAgentActivity(dsId: string) {
  problemsOnly.value = false
  setQuery({ keeper: 'activity', agent: dsId, run: undefined })
}

// The filter is in the URL, so the list reloads from a navigation — including
// one the member did not make themselves, like arriving on a pasted link.
watch(filterAgent, () => { if (isOpen.value) loadActivity() })

async function loadActivity() {
  activityLoading.value = true
  try {
    activity.value = await fetchKeeperActivity({
      data_source_id: filterAgent.value || null,
      problems_only: problemsOnly.value,
      limit: 50,
      offset: 0,
    })
  } finally {
    activityLoading.value = false
  }
}

async function loadMore() {
  // ★The offset IS how many rows are already loaded. Keeping a separate counter
  // and adding it to the length double-counts from the second page on: page 3
  // is requested at offset 150, skipping fifty runs that then never appear and
  // leave no trace that they are missing.
  const page = await fetchKeeperActivity({
    data_source_id: filterAgent.value || null,
    problems_only: problemsOnly.value,
    limit: 50,
    offset: activity.value.items.length,
  })
  activity.value = { ...page, items: [...activity.value.items, ...page.items] }
}

async function syncAll() {
  syncingAll.value = true
  try {
    syncAllResult.value = await keeperSyncAll()
    // The first sync starts server-side within a second or so; refreshing the
    // overview is what turns the toolbar button from "Synced" to "Syncing 1",
    // which is the only feedback that the button did anything at all.
    await refresh()
    await loadActivity()
  } finally {
    syncingAll.value = false
  }
}

async function loadDetail(id: string) {
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await fetchKeeperRun(id)
  } finally {
    detailLoading.value = false
  }
}

// ★A deep link may name a run that is not on the first page of the list, or not
// visible at all. The detail is fetched from the id in the URL rather than from
// the row that was clicked, so a pasted link opens the run either way — and a
// run the member may not see resolves to null and says so, rather than showing
// an expanded row with nothing in it.
watch(openRunId, id => { if (id) loadDetail(id); else detail.value = null }, { immediate: true })

watch(isOpen, open => {
  if (!open) return
  refresh()
  if (!activity.value.items.length) loadActivity()
}, { immediate: true })

watch(activeTab, tab => {
  if (tab === 'schedule' && !schedule.value) {
    fetchKeeperSchedule().then(s => { schedule.value = s })
  }
}, { immediate: true })

// ★No keydown listener and no manual focus call here — `UModal` owns Escape,
// focus trapping and the backdrop click. The overlay version hand-rolled all
// three; keeping them alongside the modal would mean two handlers racing to
// close the same dialog, and a window-level Escape listener that fires while
// some other modal is on top of this one.

const overviewCards = computed(() => [
  { key: 'runs', value: data.value.today.runs, label: t('keeper.todayRuns'), tone: 'text-gray-800 dark:text-gray-200' },
  { key: 'ok', value: data.value.today.completed, label: t('keeper.todayCompleted'), tone: 'text-green-600 dark:text-green-400' },
  { key: 'failed', value: data.value.today.failed, label: t('keeper.todayFailed'), tone: data.value.today.failed ? 'text-red-600 dark:text-red-400' : 'text-gray-800 dark:text-gray-200' },
  { key: 'tables', value: data.value.today.tables, label: t('keeper.todayTables'), tone: 'text-gray-800 dark:text-gray-200' },
])

function dotClass(result: string): string {
  if (result === 'failed') return 'bg-red-500'
  if (result === 'partial') return 'bg-amber-500'
  if (result === 'cancelled') return 'bg-gray-400'
  if (result === 'running') return 'bg-blue-500'
  return 'bg-green-500'
}

function resultTextClass(result: string): string {
  if (result === 'failed') return 'text-red-600 dark:text-red-400'
  if (result === 'partial') return 'text-amber-600 dark:text-amber-400'
  return 'text-gray-500 dark:text-gray-400'
}

function humanDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}m ${s % 60}s`
}

function eventLine(ev: any): string {
  if (typeof ev === 'string') return ev
  const at = ev?.at || ev?.timestamp || ''
  const msg = ev?.message || ev?.event || ev?.phase || JSON.stringify(ev)
  return at ? `${at}  ${msg}` : String(msg)
}
</script>
