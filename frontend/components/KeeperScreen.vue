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
                <!-- ★The ratio is shown on EVERY run that has one, not only on a
                     failure. It read `v-if="run.workspaces_failed"`, so a healthy
                     run never showed 4/4 — and "how much of my estate did this
                     actually cover" is the question the row is scanned for, on a
                     good day as much as a bad one. Amber only when some missed. -->
                <span
                  v-if="run.workspaces_total"
                  class="font-mono tabular-nums"
                  :class="run.workspaces_failed ? 'text-amber-600 dark:text-amber-400' : ''"
                  :title="$t('keeper.workspacesRatioTitle', { done: run.workspaces_done, total: run.workspaces_total })"
                >
                  {{ run.workspaces_done }}/{{ run.workspaces_total }}
                </span>
                <!-- ★The headline outcome of a sync, and it was thrown away.
                     Hidden only for a run that is still going with nothing found
                     yet: "0 tables" mid-crawl is not a fact, it is a countdown. -->
                <span v-if="run.tables != null && (run.tables || run.result !== 'running')" class="tabular-nums">
                  {{ $t('keeper.tablesN', { n: run.tables }) }}
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
                <!-- ★★★A RUNNING run used to draw nothing at all.
                     Every block below is behind a `v-if` on data that only
                     exists once the run has CLOSED — the error, the per-workspace
                     breakdown and the event log are all written by
                     `sync_runs._close`. Mid-run all three are empty, so opening a
                     row that was visibly working gave back a blank box, which
                     reads as the screen being broken rather than the sync being
                     unfinished. A live run now says what it is doing, how far it
                     has got, and shows whatever has already landed. -->
                <div v-if="detail.result === 'running'" class="mb-2 rounded-md bg-blue-50 px-2 py-1.5 dark:bg-blue-500/10">
                  <p class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] font-medium text-blue-800 dark:text-blue-300">
                    <UIcon name="i-heroicons-arrow-path" class="h-3 w-3 shrink-0 animate-spin" />
                    {{ $t('keeper.runningTitle') }}
                    <span class="font-normal">{{ phaseLabel(detail.phase) }}</span>
                    <span v-if="detail.workspaces_total" class="font-mono tabular-nums font-normal">
                      {{ detail.workspaces_done }}/{{ detail.workspaces_total }}
                    </span>
                  </p>
                  <!-- The bar is drawn only against a real total. A zero-width
                       bar next to "0/0" claims a measurement nobody has made. -->
                  <div v-if="detail.workspaces_total" class="mt-1 h-1 overflow-hidden rounded-full bg-blue-200 dark:bg-blue-500/20">
                    <div class="h-full rounded-full bg-blue-500 transition-all" :style="{ width: runningPercent + '%' }"></div>
                  </div>
                  <p class="mt-1 text-[11px] leading-snug text-blue-800/80 dark:text-blue-300/80">
                    {{ detail.workspaces && detail.workspaces.length
                      ? $t('keeper.runningLanded', { n: detail.workspaces.length })
                      : $t('keeper.runningNothingYet') }}
                  </p>
                </div>

                <p v-if="detail.error" class="mb-2 rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-snug text-red-700 dark:bg-red-500/10 dark:text-red-400">
                  {{ detail.error }}
                  <span v-if="detail.error_kind === 'infrastructure'" class="block text-red-600/80 dark:text-red-400/70">
                    {{ $t('keeper.ourSide') }}
                  </span>
                </p>

                <!-- ★A partial run is the whole reason this screen exists: the
                     sync quietly came back with less than it used to. The payload
                     carries no PREVIOUS run's table count, so nothing here claims
                     a drop against yesterday — it states the part that IS known,
                     which workspaces were missed and that the table figure counts
                     only the ones that answered. -->
                <p
                  v-if="detail.result === 'partial'"
                  class="mb-2 rounded-md bg-amber-50 px-2 py-1.5 text-[11px] leading-snug text-amber-800 dark:bg-amber-500/10 dark:text-amber-300"
                >
                  {{ $t('keeper.partialNote', {
                    missed: missedWorkspaces,
                    total: detail.workspaces_total || (detail.workspaces ? detail.workspaces.length : 0),
                    tables: detail.tables,
                  }) }}
                </p>

                <!-- Step / tables / coverage for a finished run. `phase` reaches
                     the client on every run and had never been rendered. -->
                <p
                  v-if="detail.result !== 'running'"
                  class="mb-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-gray-500 dark:text-gray-400"
                >
                  <span v-if="detail.phase">{{ $t('keeper.phaseTitle') }}: {{ phaseLabel(detail.phase) }}</span>
                  <span v-if="detail.tables != null" class="tabular-nums">{{ $t('keeper.tablesN', { n: detail.tables }) }}</span>
                  <span v-if="detail.workspaces_total" class="tabular-nums">
                    {{ $t('keeper.workspacesRatioTitle', { done: detail.workspaces_done, total: detail.workspaces_total }) }}
                  </span>
                </p>

                <div v-if="detail.workspaces && detail.workspaces.length" class="mb-2">
                  <p class="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    {{ $t('keeper.workspaces') }}
                  </p>
                  <!-- ★The tenant is identical on every row in practice, so it is
                       a heading rather than a column — repeated 63 times it is
                       noise that pushes the name and the count off the line. It
                       falls back to a per-row chip on the mixed-tenant case,
                       which is the only case where it carries information. -->
                  <p v-if="detailTenant" class="mb-1 truncate text-[11px] text-gray-500 dark:text-gray-400">
                    {{ $t('keeper.tenantLabel', { name: detailTenant }) }}
                  </p>
                  <div v-for="(ws, i) in detail.workspaces" :key="i" class="flex items-center gap-2 py-0.5 text-[11px]">
                    <span class="h-1.5 w-1.5 shrink-0 rounded-full" :class="ws.status === 'failed' ? 'bg-red-500' : 'bg-green-500'"></span>
                    <span class="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{{ ws.name || $t('keeper.unnamedWorkspace') }}</span>
                    <!-- Which workspace it sits in, and what it is. Two agents
                         legitimately hold a `DL_POC` each; the containing
                         workspace is what tells them apart. -->
                    <span
                      v-if="ws.workspace"
                      class="shrink-0 truncate rounded bg-gray-100 px-1.5 py-px text-[10px] text-gray-500 dark:bg-gray-800 dark:text-gray-400"
                      :title="$t('keeper.workspaceLabel', { name: ws.workspace })"
                    >{{ ws.workspace }}</span>
                    <span v-if="ws.kind" class="shrink-0 text-[10px] text-gray-400 dark:text-gray-500">{{ ws.kind }}</span>
                    <span v-if="!detailTenant && ws.tenant" class="max-w-[30%] shrink-0 truncate text-[10px] text-gray-400 dark:text-gray-500">{{ ws.tenant }}</span>
                    <span
                      v-if="ws.tables != null"
                      class="shrink-0 font-mono tabular-nums text-gray-400 dark:text-gray-500"
                      :title="$t('keeper.tablesTitle')"
                    >{{ ws.tables }}</span>
                    <span v-if="ws.error" class="max-w-[50%] shrink-0 truncate text-red-600 dark:text-red-400">{{ ws.error }}</span>
                  </div>
                </div>

                <div v-if="detail.events && detail.events.length">
                  <p class="mb-1 text-[10px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    {{ $t('keeper.events') }}
                  </p>
                  <div class="max-h-48 overflow-y-auto rounded-md bg-gray-50 p-2 dark:bg-gray-900">
                    <p
                      v-for="(ev, i) in detail.events"
                      :key="i"
                      class="flex items-baseline gap-2 font-mono text-[10px] leading-relaxed text-gray-600 dark:text-gray-400"
                    >
                      <!-- ★Offset from the run's own start, not a wall clock: a
                           sync is read as "where did the 86 seconds go", and a
                           column of near-identical HH:MM:SS answers that far
                           worse than +00:16. Empty while `ts` is null — the
                           tracker keeps no per-workspace timestamp yet — and an
                           absent span is the right rendering of an absent fact,
                           never the string "null". -->
                      <span v-if="eventOffset(ev)" class="shrink-0 tabular-nums text-gray-400 dark:text-gray-500">{{ eventOffset(ev) }}</span>
                      <span class="min-w-0 flex-1">{{ eventLine(ev) }}</span>
                      <span
                        v-if="eventProgress(ev)"
                        class="shrink-0 tabular-nums text-gray-400 dark:text-gray-500"
                        :title="$t('keeper.workspacesRatioTitle', { done: ev.done, total: ev.total })"
                      >{{ eventProgress(ev) }}</span>
                    </p>
                  </div>
                </div>

                <!-- ★Last resort, and it must exist. Every block above is
                     conditional, so "no block matched" is reachable — and an
                     expanded row with nothing in it is the defect this whole
                     panel was rewritten for. If there is genuinely nothing, say
                     so in words. -->
                <p v-if="!detailHasAnything" class="py-2 text-xs text-gray-500 dark:text-gray-400">
                  {{ $t('keeper.nothingRecordedYet') }}
                </p>
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
              <!-- Was the raw column value — "ingesting" is a schema word, not
                   something to show a member. -->
              <span v-if="run.phase" class="shrink-0 text-gray-500 dark:text-gray-400">{{ phaseLabel(run.phase) }}</span>
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

// ★`te` as well as `t`: the phase vocabulary lives in the backend and can gain a
// word without this file being touched. `t` on a missing key renders the key
// itself ("keeper.phase.compacting"), which looks like a bug in the product;
// `te` lets an unknown phase fall back to its own raw value instead.
const { t, te } = useI18n()
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

/** What the member is told a backend phase word means.
 *
 *  The five the tracker writes are `discovering`, `ingesting`, `learning`,
 *  `done` and `error` (`app/services/connection_sync_progress.py` and
 *  `sync_runs.begin`). ★An unknown sixth shows its RAW value rather than a
 *  missing-key string — a word the member does not recognise is a far smaller
 *  failure than a screen printing "keeper.phase.x" at them. */
function phaseLabel(phase: string | null | undefined): string {
  if (!phase) return t('keeper.phaseUnknown')
  const key = `keeper.phase.${phase}`
  return te(key) ? t(key) : phase
}

function eventLine(ev: any): string {
  if (typeof ev === 'string') return ev
  // ★No timestamp in here any more — the time and the progress counter are
  // their own spans, so an absent one leaves no stray separator behind in the
  // middle of the message.
  const msg = ev?.message || ev?.event || ev?.phase || JSON.stringify(ev)
  return String(msg)
}

/** `+MM:SS` since the run started, or '' when there is no usable timestamp.
 *
 *  ★The field is `ts`. This read `ev.at || ev.timestamp`, neither of which the
 *  API has ever sent, so no event ever showed a time — a two-sided bug, since
 *  the tracker was not stamping one either. Both halves are fixed:
 *  `connection_sync_progress.endpoint_done` now stamps `ts` as each workspace
 *  completes, and `sync_runs._events_from_detail` carries it through.
 *
 *  ★Runs recorded BEFORE that still have `ts: null` for ever, deliberately —
 *  no backfill, because a plausible invented time is worse than an absent one.
 *  So the empty string stays a normal case on old rows and must render as
 *  nothing at all, never as "null". */
function eventOffset(ev: any): string {
  const ts = ev?.ts || ev?.at || ev?.timestamp
  const started = detail.value?.started_at
  if (!ts) return ''
  const at = Date.parse(String(ts).endsWith('Z') ? String(ts) : `${ts}Z`)
  if (Number.isNaN(at)) return ''
  if (!started) return ''
  const from = Date.parse(String(started).endsWith('Z') ? String(started) : `${started}Z`)
  if (Number.isNaN(from)) return ''
  const s = Math.max(0, Math.round((at - from) / 1000))
  const m = Math.floor(s / 60)
  return `+${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

/** "1/4" — the per-event progress the payload has always carried and nothing
 *  displayed. Digits only, so it needs no translation; the title does. */
function eventProgress(ev: any): string {
  if (!ev || typeof ev !== 'object') return ''
  if (ev.total == null || ev.done == null) return ''
  if (!ev.total) return ''
  return `${ev.done}/${ev.total}`
}

const runningPercent = computed(() => {
  const d = detail.value
  if (!d || !d.workspaces_total) return 0
  return Math.min(100, Math.round(((d.workspaces_done || 0) / d.workspaces_total) * 100))
})

/** How many workspaces this run did not get. Prefers the breakdown, because the
 *  counter and the list are written from different fields and the list is the
 *  one that can be pointed at. */
const missedWorkspaces = computed(() => {
  const d = detail.value
  if (!d) return 0
  const failed = (d.workspaces || []).filter(w => w?.status === 'failed').length
  if (failed) return failed
  if (d.workspaces_failed) return d.workspaces_failed
  return Math.max(0, (d.workspaces_total || 0) - (d.workspaces_done || 0))
})

/** The one tenant every workspace in this run belongs to, or null when they
 *  differ — in which case the tenant goes back on the rows, where it means
 *  something. */
const detailTenant = computed<string | null>(() => {
  const names = new Set(
    (detail.value?.workspaces || []).map(w => w?.tenant).filter(Boolean) as string[]
  )
  return names.size === 1 ? [...names][0] : null
})

/** Whether the expanded panel has ANY block to draw. Guards the one outcome
 *  this rewrite exists to remove: an expanded row that is a blank box. */
const detailHasAnything = computed(() => {
  const d = detail.value
  if (!d) return false
  return !!(
    d.result === 'running'
    || d.error
    || d.phase
    || d.tables != null
    || d.workspaces_total
    || (d.workspaces && d.workspaces.length)
    || (d.events && d.events.length)
  )
})
</script>
