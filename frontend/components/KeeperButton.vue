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
const { t } = useI18n()
const { relativeTime } = useRelativeTime()
const { state, workingCount, problemCount, lastActivityAt } = useKeeper()

defineEmits<{ open: [] }>()

// ★The state must be legible without opening anything — that is the entire
// justification for a button rather than a nav item. Colour alone does not do
// that (it fails for anyone who cannot distinguish it, and it fails in a
// screenshot), so shape, label and title all carry it too.
const icon = computed(() => ({
  working: 'i-heroicons-arrow-path',
  attention: 'i-heroicons-exclamation-triangle',
  resting: 'i-heroicons-check-circle',
  hidden: '',
}[state.value]))

const label = computed(() => {
  if (state.value === 'working') return t('keeper.labelWorking', { n: workingCount.value })
  if (state.value === 'attention') return t('keeper.labelAttention', { n: problemCount.value })
  return t('keeper.labelResting')
})

const summary = computed(() => {
  if (state.value === 'working') return t('keeper.tipWorking', { n: workingCount.value })
  if (state.value === 'attention') return t('keeper.tipAttention', { n: problemCount.value })
  return lastActivityAt.value
    ? t('keeper.tipResting', { when: relativeTime(lastActivityAt.value) })
    : t('keeper.neverSynced')
})

const buttonClass = computed(() => ({
  working: 'border-blue-200 dark:border-blue-500/30 bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-500/20',
  attention: 'border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 hover:bg-amber-100 dark:hover:bg-amber-500/20',
  resting: 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800/50',
  hidden: '',
}[state.value]))
</script>
