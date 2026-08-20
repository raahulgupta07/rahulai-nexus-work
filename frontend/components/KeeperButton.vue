<template>
  <!-- `hidden` is a state, so there is deliberately nothing to render for it. -->
  <button
    v-if="state !== 'hidden'"
    type="button"
    class="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-lg border text-xs font-medium whitespace-nowrap transition-colors"
    :class="buttonClass"
    :title="summary"
    :aria-label="summary"
    @click="$emit('open')"
  >
    <UIcon :name="icon" class="w-3.5 h-3.5" :class="state === 'working' ? 'animate-spin' : ''" />
    <span>{{ label }}</span>
  </button>
</template>

<script setup lang="ts">
// The sync-status button that sits in the Agents toolbar.
//
// ★One click. This used to open a small popover whose only real action was a
// "See all activity" link — a menu in front of a menu, so reaching the history
// took two clicks and the first one showed a summary nobody asked for. Every
// fact that panel carried (today's counts, the recent list, what needs a
// person) is a tab on the history itself, so the panel was duplicating the
// screen it was standing in front of. The button now opens the screen.
//
// ★It reads its state, it never receives it. Every fact on this button comes
// from `useKeeper`, which is the same feed the history screen reads — so the
// button and the screen cannot disagree about whether something is running.
//
// ★"Paused" is not a state here. Pausing the keeper is not built (it needs a
// backend decision about what a paused schedule does to a sign-in-triggered
// sync), and a fourth chip that never appears is a lie in the legend.
//
// ★★★It is called Activity, and it used to be called "Synced".
// "Synced" was the FALLBACK label — every situation that was not running and
// not flagged printed it, including an installation that had never synced
// anything at all. The word was also wrong in kind: this control opens a
// screen, so it should name the place it opens, and "Synced" is a claim about
// state that the states beside it (`Syncing 2`, `2 need you`) are not making.
// `useKeeper.KeeperState` now separates the four situations that shared it.
const { t } = useI18n()
const { relativeTime } = useRelativeTime()
const {
  state, workingCount, problemCount, failedCount, lastActivityAt, data,
} = useKeeper()

defineEmits<{ open: [] }>()

// ★The state must be legible without opening anything — that is the entire
// justification for a button rather than a nav item. Colour alone does not do
// that (it fails for anyone who cannot distinguish it, and it fails in a
// screenshot), so shape, label and title all carry it too.
const icon = computed(() => ({
  working: 'i-heroicons-arrow-path',
  attention: 'i-heroicons-exclamation-triangle',
  failed: 'i-heroicons-x-circle',
  // ★Not a check mark, and not a warning either. Nothing has synced yet, which
  // on a new installation is the ordinary state of affairs and not a fault —
  // an empty tray says "not yet" without accusing anybody of anything.
  //
  // ★And not the same glyph as `stale`, though both are quiet and grey. Those
  // two are the pair most likely to be told apart by colour alone, because
  // neither is coloured: if they shared an icon, only the words would separate
  // "has never run" from "ran, a while ago" — and the words are the first thing
  // a hurried reader skips.
  never: 'i-heroicons-inbox',
  stale: 'i-heroicons-clock',
  resting: 'i-heroicons-check-circle',
  hidden: '',
}[state.value]))

/** How far the running syncs have got, as `3 of 7`, or '' when nothing has
 *  measured it yet.
 *
 *  ★Summed across every sync in flight, because the count beside it is too:
 *  "Syncing 2 · 3 of 7" is two agents and seven workspaces between them. Empty
 *  while `workspaces_total` is 0 — a member starting a crawl sees `Syncing 1`
 *  for the first few seconds rather than a confident `0 of 0`. */
const runningProgress = computed(() => {
  const runs = data.value.working_now
  const total = runs.reduce((n, r) => n + (r.workspaces_total || 0), 0)
  if (!total) return ''
  const done = runs.reduce((n, r) => n + (r.workspaces_done || 0), 0)
  return t('keeper.progressRatio', { done, total })
})

// ★Every label carries a fact, and no label carries one that is not known.
// The old resting label carried none at all, which is how it survived being
// wrong for so long: there was nothing in it that could be checked.
const label = computed(() => {
  const when = lastActivityAt.value ? relativeTime(lastActivityAt.value) : ''
  switch (state.value) {
    case 'working': {
      const head = t('keeper.labelWorking', { n: workingCount.value })
      const ratio = runningProgress.value
      return ratio ? `${head} · ${ratio}` : head
    }
    case 'attention': return t('keeper.labelAttention', { n: problemCount.value })
    case 'failed': return t('keeper.labelFailed', { n: failedCount.value })
    case 'never': return t('keeper.labelNever')
    case 'stale': return t('keeper.labelStale', { when })
    default: return when ? t('keeper.labelUpToDate', { when }) : t('keeper.labelNever')
  }
})

const summary = computed(() => {
  const when = lastActivityAt.value ? relativeTime(lastActivityAt.value) : ''
  switch (state.value) {
    case 'working': return t('keeper.tipWorking', { n: workingCount.value })
    case 'attention': return t('keeper.tipAttention', { n: problemCount.value })
    case 'failed': return t('keeper.tipFailed', { n: failedCount.value })
    case 'never': return t('keeper.tipNever')
    case 'stale': return t('keeper.tipStale', { when })
    default: return when ? t('keeper.tipResting', { when }) : t('keeper.tipNever')
  }
})

// ★Colour is the second carrier, never the only one — the label already says
// which state this is. `never` and `stale` are deliberately QUIET: neither is a
// fault, and painting a new installation amber for not having synced yet
// teaches members to ignore the colour that matters.
const buttonClass = computed(() => ({
  working: 'border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-500/20',
  attention: 'border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-500/20',
  failed: 'border-red-200 dark:border-red-500/30 bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/20',
  never: 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800/50',
  stale: 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50',
  resting: 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50',
  hidden: '',
}[state.value]))
</script>
