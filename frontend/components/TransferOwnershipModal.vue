<template>
  <UModal v-model="open" :prevent-close="saving">
    <div class="p-5 relative">
      <button
        :disabled="saving"
        class="absolute top-3 end-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 outline-none disabled:opacity-50"
        @click="close"
      >
        <UIcon name="i-heroicons-x-mark" class="w-4 h-4" />
      </button>

      <h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">
        {{ title }}
      </h3>
      <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
        {{ $t('ownership.transfer.subtitle') }}
      </p>

      <!-- Recipient -->
      <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mt-4 mb-1.5">
        {{ $t('ownership.transfer.recipientLabel') }}
      </label>
      <USelectMenu
        v-model="recipient"
        :options="recipientOptions"
        option-attribute="label"
        by="id"
        searchable
        :search-attributes="['label', 'email']"
        :placeholder="$t('ownership.transfer.recipientPlaceholder')"
        data-testid="transfer-recipient"
      />
      <p class="text-[11px] text-gray-400 dark:text-gray-500 mt-1.5">
        {{ $t('ownership.transfer.recipientHint') }}
      </p>

      <!-- What is moving -->
      <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mt-4 mb-1.5">
        {{ $t('ownership.transfer.movingLabel') }}
      </label>
      <div class="border border-gray-200 dark:border-gray-700 rounded-lg divide-y divide-gray-100 dark:divide-gray-800 max-h-40 overflow-y-auto">
        <div
          v-for="item in movingItems"
          :key="item.id"
          class="px-3 py-2 text-sm text-gray-800 dark:text-gray-200 flex items-center gap-2"
        >
          <span class="truncate">{{ item.title || $t('ownership.untitled') }}</span>
          <span v-if="item.has_schedule" class="text-[11px] text-amber-600 dark:text-amber-400 shrink-0">
            {{ $t('ownership.transfer.hasSchedule') }}
          </span>
          <span v-if="item.shared_with_count" class="text-[11px] text-gray-400 shrink-0">
            {{ $t('ownership.transfer.sharedWith', { count: item.shared_with_count }) }}
          </span>
        </div>
        <div v-if="!movingItems.length" class="px-3 py-2 text-sm text-gray-400">
          {{ $t('ownership.transfer.everything') }}
        </div>
      </div>

      <!-- Keep access. Default ON: handing over is not the same as walking
           away, and a handover that instantly locks you out of your own work
           is one people quietly avoid doing. -->
      <label v-if="!isAdminMode" class="flex items-start gap-2.5 mt-4 cursor-pointer">
        <UCheckbox v-model="keepAccess" />
        <span class="text-sm text-gray-800 dark:text-gray-200 leading-snug">
          {{ $t('ownership.transfer.keepAccess') }}
          <span class="block text-[11px] text-gray-400 dark:text-gray-500">
            {{ $t('ownership.transfer.keepAccessHint') }}
          </span>
        </span>
      </label>

      <!-- ★The consequence that is not a column change. A report with
           shared_run_identity='creator' queries its data AS its owner, using
           that person's data-source sign-in. Saying so before they confirm is
           the whole reason the backend reports this count separately. -->
      <div
        v-if="runsAsOwnerCount"
        class="mt-4 rounded-lg border border-blue-200 dark:border-blue-900 bg-blue-50/60 dark:bg-blue-500/10 px-3 py-2.5"
        data-testid="transfer-run-identity-warning"
      >
        <p class="text-xs text-blue-900 dark:text-blue-200 leading-relaxed">
          {{ $t('ownership.transfer.runIdentityWarning', { count: runsAsOwnerCount, name: recipientName }) }}
        </p>
      </div>

      <!-- ★A caution, not information — hence amber beside the blue sibling
           above. Owning one of these agents is a CAPABILITY, and the move
           splits two ways: a Fabric / Power BI agent stops answering until the
           recipient signs in themselves, while every other per-user kind hands
           them the organization's shared sign-in instead. Both halves are in
           the copy on purpose: whoever is choosing a recipient is the only
           person who can judge which outcome they want, and neither is
           recoverable by reading the screen afterwards. -->
      <div
        v-if="credentialBoundCount"
        class="mt-4 rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50/70 dark:bg-amber-500/10 px-3 py-2.5"
        data-testid="transfer-credentials-warning"
      >
        <p class="text-xs text-amber-900 dark:text-amber-200 leading-relaxed">
          {{ $t('ownership.transfer.credentialsWarning', { count: credentialBoundCount, name: recipientName }) }}
        </p>
      </div>

      <!-- ★★★What will NOT move, said in the same breath as what will. In admin
           mode the automatic path moves only reports that have a dashboard, a
           schedule or a share; a report that is nothing but a chat thread stays
           with the person it belongs to, because reports are gated `owner_only`
           as conversation privacy and handing those chats to a colleague cannot
           also be right.
           ★Both numbers or neither — a line naming only what moves reads as a
           complete promise, and that is the reason somebody later asks where
           their conversations went. The second sentence exists so "stay with
           them" is never read as "were deleted". -->
      <div
        v-if="isAdminMode && conversationSplit"
        class="mt-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 px-3 py-2.5"
        data-testid="transfer-conversation-split"
      >
        <p class="text-xs text-gray-700 dark:text-gray-300 leading-relaxed">
          {{ $t('ownership.transfer.splitLine', conversationSplit) }}
        </p>
        <p class="text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed mt-1">
          {{ $t('ownership.transfer.splitHint') }}
        </p>
      </div>

      <p v-if="error" class="mt-3 text-xs text-red-600 dark:text-red-400">{{ error }}</p>

      <div class="flex justify-end gap-2 mt-5">
        <UButton color="gray" variant="ghost" :disabled="saving" @click="close">
          {{ $t('common.cancel') }}
        </UButton>
        <UButton
          color="blue"
          :loading="saving"
          :disabled="!recipient"
          data-testid="transfer-confirm"
          @click="confirm"
        >
          {{ confirmLabel }}
        </UButton>
      </div>
    </div>
  </UModal>
</template>

<script setup lang="ts">
import type { ContentSummary, OwnedItem } from '~/composables/useOwnership'

const props = defineProps<{
  modelValue: boolean
  // Empty means "everything I own".
  items?: OwnedItem[]
  // Single-report mode: hand over just this one, from the report itself.
  reportId?: string
  // ★Admin mode: move everything ANOTHER person owns. Distinct props rather
  // than a boolean flag, because the two modes hit different routes with
  // different gates — a flag would let a missing id silently fall back to the
  // member path and move the wrong person's work.
  membershipId?: string
  organizationId?: string
  fromName?: string
  // ★The counts that are NOT per-item. `runs_as_owner` rides on each OwnedItem
  // because it is a property of a report, but `credential_bound_data_sources`
  // is a property of the whole body of work — there is no agent row in
  // `items`, and in admin mode `items` is empty by design. So the parent hands
  // in the summary it already holds rather than this dialog opening a second
  // request for a number the screen behind it has fetched already.
  summary?: ContentSummary | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'transferred', batchId: string): void
}>()

const { t } = useI18n()
const toast = useToast()
const { saving, error, handOver, handOverReport, transferMemberContent } = useOwnership()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// ★Declared ABOVE every reader. A const declared below a computed that reads it
// is safe only while the computed stays lazy — and this fork has already lost a
// release to exactly that, when an upstream `watch(..., { immediate: true })`
// evaluated a computed during setup() and hit a const still in its temporal
// dead zone. Cheap to keep in the right order; expensive to discover.
const keepAccess = ref(true)

const movingItems = computed(() => props.items || [])

const runsAsOwnerCount = computed(
  () => movingItems.value.filter((i) => i.runs_as_owner).length,
)

// ★Read straight off the summary, never derived from `movingItems`: the item
// list is reports, and this counts agents. An absent summary means the parent
// has not fetched one — the warning then stays hidden, which is the same
// behaviour as a genuine zero. That is deliberate: this dialog must not open a
// request of its own on a screen that is already loading.
const credentialBoundCount = computed(
  () => props.summary?.credential_bound_data_sources || 0,
)

// ★The two halves of what an offboarding transfer does, or `null` when there is
// nothing to say. Read off the summary for the same reason as the count above —
// `items` is empty by design in admin mode — and returned as ONE object so the
// template can never render half of it. `conversation_reports` is a sub-count of
// `reports`, so what moves is the difference; clamped at zero so a summary from
// an older backend (field absent → 0) reports the plain report count rather than
// a negative one.
const conversationSplit = computed(() => {
  const s = props.summary
  if (!s) return null
  const staying = s.conversation_reports || 0
  const moving = Math.max(0, (s.reports || 0) - staying)
  if (!moving && !staying) return null
  return { moving, staying }
})

// Admin mode names the person whose work is moving. "Hand over your content"
// on somebody else's row would be plainly wrong, and this wording is the only
// thing on screen that says whose work is being moved.
const isAdminMode = computed(() => Boolean(props.membershipId && props.organizationId))

const title = computed(() => {
  if (isAdminMode.value) return t('ownership.transfer.titleFrom', { name: props.fromName || '' })
  // ★A single item is its own sentence. `titleN` reads "Hand over 1 items"
  // otherwise, and this dialog is opened with exactly one item from both the
  // report menu and a one-row selection in My content.
  if (movingItems.value.length === 1) return t('ownership.transfer.titleOne')
  return movingItems.value.length
    ? t('ownership.transfer.titleN', { count: movingItems.value.length })
    : t('ownership.transfer.titleAll')
})

const confirmLabel = computed(() => {
  if (isAdminMode.value) return t('ownership.transfer.confirmAll')
  if (movingItems.value.length === 1) return t('ownership.transfer.confirmOne')
  return movingItems.value.length
    ? t('ownership.transfer.confirmN', { count: movingItems.value.length })
    : t('ownership.transfer.confirmAll')
})

// ── recipients ────────────────────────────────────────────────────────────
// Same source the share picker uses, so the two screens can never disagree
// about who exists.
const members = ref<{ id: string; label: string; email: string }[]>([])
const recipient = ref<{ id: string; label: string; email: string } | null>(null)
const recipientName = computed(() => recipient.value?.label || recipient.value?.email || '')

const { data: session } = useAuth()

const recipientOptions = computed(() =>
  // Never offer yourself: the backend refuses it, and an option that always
  // errors is worse than one that is absent.
  members.value.filter((m) => m.id !== String((session.value as any)?.id || '')),
)

const fetchMembers = async () => {
  try {
    const res = await useMyFetch('/organization/members')
    if (res.data.value) {
      members.value = (res.data.value as any[]).map((u: any) => ({
        id: String(u.id),
        label: u.name || u.email,
        email: u.email,
      }))
    }
  } catch {
    // A picker with no options renders its own empty state; a toast here
    // would fire on every open in an org of one.
  }
}

// ★★★`immediate: true`, for the same reason as RemoveMemberModal. Both admin
// callers render this under a `v-if` and open it in the same tick, so the
// watcher is created with `open` already true and a plain watch never fires.
// The failure is quiet and specific: the dialog opens, the title is right, and
// the recipient picker is permanently EMPTY — the one control the whole dialog
// exists to provide. Nothing is logged and no request is made.
//
// ★Safe only because `fetchMembers` and every ref it writes are declared above.
watch(open, (isOpen) => {
  if (isOpen) {
    recipient.value = null
    error.value = ''
    fetchMembers()
  }
}, { immediate: true })

const close = () => {
  if (!saving.value) open.value = false
}

const confirm = async () => {
  if (!recipient.value) return
  let result
  if (props.membershipId && props.organizationId) {
    result = await transferMemberContent(
      props.organizationId,
      props.membershipId,
      recipient.value.id,
    )
  } else if (props.reportId) {
    result = await handOverReport(props.reportId, recipient.value.id, keepAccess.value)
  } else {
    result = await handOver(recipient.value.id, {
      reportIds: movingItems.value.map((i) => i.id),
      keepAccess: keepAccess.value,
    })
  }

  if (!result) return

  // ★The confirmation quotes the RESULT, never the summary: `conversations_left_behind`
  // is what the batch actually left, while `conversation_reports` was the
  // estimate rendered before anyone confirmed. They agree in the normal case and
  // the moment they do not, the estimate is the wrong one to repeat back.
  // ★And it is only ever added, never substituted — the count that moved has to
  // stay on screen, or this becomes a line naming only what did NOT move, which
  // is the same half-promise from the other end.
  const left = result.conversations_left_behind || 0
  toast.add({
    title: left
      ? t('ownership.transfer.doneLeftBehind', {
          count: result.total,
          name: recipientName.value,
          left,
        })
      : t('ownership.transfer.done', {
          count: result.total,
          name: recipientName.value,
        }),
    color: 'green',
  })
  emit('transferred', result.batch_id)
  open.value = false
}
</script>
