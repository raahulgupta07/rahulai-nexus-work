<template>
  <!-- Nothing to say → render nothing at all. A strip that is always there is
       noise, and noise is what makes people stop reading it the one time it
       matters. -->
  <div v-if="visible">

    <!-- chip: for a dense list row or the chat picker.
         ★A DOT, not a worded badge. The agent rail is ~250px wide and already
         carries a sign-in badge and an on/off switch; a worded chip took so
         much of it that "Microsoft Fabric" was truncated to "M". The agent's
         name is the thing somebody is actually looking for, so the state gets
         the smallest mark that still reads at a glance and puts the words in
         the tooltip. The full sentence lives on the agent page. -->
    <span
      v-if="variant === 'chip'"
      class="inline-flex items-center flex-none"
      :title="chipLabel + ' — ' + tooltip"
      :aria-label="chipLabel"
      role="img"
    >
      <span
        class="w-2 h-2 rounded-full ring-2 ring-white dark:ring-gray-900"
        :class="[dotClass, isRunning ? 'animate-pulse' : '']"
      ></span>
    </span>

    <!-- strip: the agent page header. One line, the counts, and the action. -->
    <div
      v-else
      class="flex items-center gap-3 flex-wrap px-3 py-2 rounded-lg border text-xs"
      :class="stripClass"
    >
      <span class="inline-flex items-center gap-1.5 font-medium flex-none" :class="toneText">
        <Icon v-if="isExpired" name="heroicons:key" class="w-3.5 h-3.5" />
        <Icon v-else-if="isRunning" name="heroicons:arrow-path" class="w-3.5 h-3.5 animate-spin" />
        <Icon v-else-if="isPartial" name="heroicons:exclamation-triangle" class="w-3.5 h-3.5" />
        <Icon v-else-if="isError" name="heroicons:exclamation-circle" class="w-3.5 h-3.5" />
        <Icon v-else name="heroicons:check-circle" class="w-3.5 h-3.5" />
        {{ chipLabel }}
      </span>

      <span class="text-gray-600 dark:text-gray-300 min-w-0 flex-1">{{ message }}</span>

      <div v-if="isRunning" class="w-24 h-1 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden flex-none">
        <div class="h-full bg-blue-500 transition-all duration-500" :style="{ width: percent + '%' }"></div>
      </div>

      <!-- Reconnect, not Retry: a crawl cannot fix a credential Microsoft has
           stopped honouring, and offering "Try again" there sends the member
           round a loop that can only fail. -->
      <UButton
        v-if="isExpired"
        size="2xs"
        color="blue"
        class="flex-none"
        @click="emit('reconnect')"
      >
        {{ $t('data.syncReconnect') }}
      </UButton>
      <UButton
        v-else-if="showRetry"
        size="2xs"
        color="gray"
        variant="soft"
        :loading="retrying"
        class="flex-none"
        @click="retry"
      >
        {{ $t('data.syncTryAgain') }}
      </UButton>
    </div>

    <!-- Which workspaces did not answer. Shown under the strip on a partial
         result, because "3 of 4" is only useful if you can see which one. -->
    <div
      v-if="variant !== 'chip' && isPartial && failed.length"
      class="mt-1.5 ps-3 text-[11px] text-gray-500 dark:text-gray-400 space-y-0.5"
    >
      <div v-for="d in failed" :key="d.name">
        <span class="font-medium text-gray-700 dark:text-gray-300">{{ d.name }}</span>
        — {{ d.error || $t('data.syncDidNotAnswer') }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConnectionSync } from '~/composables/useConnectionSync'

const { t } = useI18n()

const props = withDefaults(defineProps<{
  dataSource: any
  /** 'chip' for list rows and the picker; 'strip' for a page header. */
  variant?: 'chip' | 'strip'
}>(), { variant: 'strip' })

const emit = defineEmits<{ (e: 'reconnect'): void }>()

const dsRef = computed(() => props.dataSource)
const {
  applies, state, refresh, isRunning, isLearning, isPartial, isError, isDone,
  unitPlural, percent, hasSomethingToSay,
} = useConnectionSync(dsRef)

// The composable reports the raw unit ('workspaces' / 'tenants'); the label is
// resolved here because that is where a locale is in scope. Keeping i18n out of
// the composable stops a data helper depending on the UI layer.
const unitLabel = computed(() =>
  unitPlural.value === 'workspaces' ? t('data.syncUnitWorkspaces') : t('data.syncUnitTenants'),
)

const retrying = ref(false)
const failed = computed(() => (state.value.detail || []).filter((d: any) => d.status === 'failed'))

// Credential lifecycle rides on the data source (or its first connection), and
// the server derives `expired` / `expiring_soon` / `expires_in_days` from the
// stored expiry so every surface agrees about whether a sign-in still works.
const userStatus = computed<any>(() =>
  props.dataSource?.user_status || props.dataSource?.connections?.[0]?.user_status || null,
)
const isExpired = computed(() => userStatus.value?.expired === true)
const isExpiringSoon = computed(() => userStatus.value?.expiring_soon === true)
const daysLeft = computed<number | null>(() => {
  const d = userStatus.value?.expires_in_days
  return typeof d === 'number' ? d : null
})

const chipLabel = computed(() => {
  // ★Expiry outranks sync state. A "Ready" badge over a credential that no
  // longer works is the worst of both: it reads as fine and answers nothing.
  if (isExpired.value) return t('data.syncStatExpired')
  // ★Before isRunning — learning IS a running state, so a plain isRunning
  // check would swallow it and report "Reading" through the whole learn.
  if (isLearning.value) return t('data.syncStatLearning')
  if (isRunning.value) return t('data.syncStatSyncing')
  if (isPartial.value) return t('data.syncStatPartial')
  if (isError.value) return t('data.syncStatFailed')
  return t('data.syncStatReady')
})

/**
 * One sentence: what is true, in units the member can check. "3 of 4
 * workspaces" can be compared against the access they know they have; a
 * percentage cannot.
 */
const message = computed(() => {
  const s = state.value
  if (isExpired.value) return t('data.syncExpiredBody')
  // Reading has finished by now; say what it is doing with what it read.
  if (isLearning.value) return t('data.syncLearningBody', { n: s.tables })
  if (isRunning.value) {
    if (s.phase === 'discovering') return t('data.syncFinding', { unit: unitLabel.value })
    const settled = s.endpoints_done + s.endpoints_failed
    const head = t('data.syncReading', {
      unit: unitLabel.value, done: settled, total: s.endpoints_total,
    })
    return s.tables ? `${head} · ${t('data.syncTablesSoFar', { n: s.tables })}` : head
  }
  if (isPartial.value) {
    return t('data.syncPartialSummary', {
      done: s.endpoints_done, total: s.endpoints_total, unit: unitLabel.value, n: s.tables,
      which: s.endpoints_failed === 1 ? t('data.syncWhichOne') : t('data.syncWhichMany'),
    })
  }
  if (isError.value) return s.error || t('data.syncFailedUnknown')
  const soon = isExpiringSoon.value
    ? ' · ' + (daysLeft.value === 1
        ? t('data.syncExpiresInDay')
        : t('data.syncExpiresInDays', { n: daysLeft.value ?? 0 }))
    : ''
  const base = s.tables
    ? t('data.syncSummaryTables', { n: s.tables, done: s.endpoints_done, unit: unitLabel.value })
    : t('data.syncConnectedPlain')
  return `${base}${lastDone.value}${soon}`
})

const lastDone = computed(() => {
  const iso = state.value.last_done_at
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (!then) return ''
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000))
  if (mins < 1) return ' · ' + t('data.syncJustNow')
  if (mins < 60) return ' · ' + t('data.syncMinsAgo', { n: mins })
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return ' · ' + t('data.syncHoursAgo', { n: hrs })
  return ' · ' + t('data.syncDaysAgo', { n: Math.round(hrs / 24) })
})

const tooltip = computed(() => message.value)

/**
 * ★An expired sign-in must show even when this member has NO sync history —
 * the composable's own `hasSomethingToSay` only knows about syncs, and a
 * credential that quietly stopped working is exactly the case where silence is
 * worst. Kept as a separate computed so the composable stays about progress.
 */
const visible = computed(() =>
  hasSomethingToSay.value || (applies.value && (isExpired.value || isExpiringSoon.value)),
)

// A retry is offered only where it would actually help. Re-running a crawl that
// just succeeded is a Microsoft rate limit spent on nothing.
const showRetry = computed(() => !isExpired.value && (isPartial.value || isError.value))

const toneText = computed(() => {
  if (isExpired.value) return 'text-red-700 dark:text-red-400'
  if (isExpiringSoon.value) return 'text-amber-700 dark:text-amber-400'
  if (isRunning.value) return 'text-blue-700 dark:text-blue-300'
  if (isPartial.value) return 'text-amber-700 dark:text-amber-400'
  if (isError.value) return 'text-red-700 dark:text-red-400'
  return 'text-green-700 dark:text-green-400'
})

const dotClass = computed(() => {
  if (isExpired.value) return 'bg-red-500'
  if (isExpiringSoon.value) return 'bg-amber-500'
  if (isRunning.value) return 'bg-blue-500'
  if (isPartial.value) return 'bg-amber-500'
  if (isError.value) return 'bg-red-500'
  return 'bg-green-500'
})

const chipClass = computed(() => {
  if (isExpired.value) return 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
  if (isExpiringSoon.value) return 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
  if (isRunning.value) return 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300'
  if (isPartial.value) return 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
  if (isError.value) return 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
  return 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300'
})

const stripClass = computed(() => {
  if (isExpired.value) return 'border-red-200 bg-red-50/60 dark:border-red-900/50 dark:bg-red-900/15'
  if (isExpiringSoon.value) return 'border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-900/15'
  if (isRunning.value) return 'border-blue-200 bg-blue-50/60 dark:border-blue-900/50 dark:bg-blue-900/15'
  if (isPartial.value) return 'border-amber-200 bg-amber-50/60 dark:border-amber-900/50 dark:bg-amber-900/15'
  if (isError.value) return 'border-red-200 bg-red-50/60 dark:border-red-900/50 dark:bg-red-900/15'
  return 'border-gray-200 bg-gray-50/60 dark:border-gray-700 dark:bg-gray-800/40'
})

async function retry() {
  if (retrying.value) return
  retrying.value = true
  try {
    const t = props.dataSource?.type
      || props.dataSource?.connection?.type
      || props.dataSource?.connections?.[0]?.type
    const base = t === 'fabric_user' ? 'fabric-signin' : 'user-signin'
    await useMyFetch(`/data_sources/${props.dataSource.id}/${base}/resync`, { method: 'POST' })
    await refresh()
  } catch (e) {
    // The strip already shows the failed state; a toast on top of it would say
    // the same thing twice.
  } finally {
    retrying.value = false
  }
}
</script>
