<template>
  <UModal v-model="open" :prevent-close="busy">
    <div class="p-5 relative" data-testid="remove-member-modal">
      <button
        :disabled="busy"
        class="absolute top-3 end-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 outline-none disabled:opacity-50"
        @click="close"
      >
        <UIcon name="i-heroicons-x-mark" class="w-4 h-4" />
      </button>

      <h3 class="text-base font-semibold text-gray-900 dark:text-gray-100">
        {{ $t('ownership.remove.title', { name: memberName }) }}
      </h3>
      <p class="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-relaxed max-w-prose">
        {{ $t('ownership.remove.body') }}
      </p>

      <!-- What they own. Loaded on open; a failure renders nothing rather than
           a broken dialog, because removal must stay possible either way. -->
      <div
        v-if="summary && summary.total > 0"
        class="mt-4 rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50/70 dark:bg-amber-500/10 px-3.5 py-3"
        data-testid="remove-owns-warning"
      >
        <div class="text-xs font-semibold text-amber-800 dark:text-amber-300 flex items-center gap-1.5">
          <UIcon name="i-heroicons-exclamation-triangle" class="w-4 h-4 shrink-0" />
          {{ $t('ownership.remove.ownsTitle', { count: summary.total }) }}
        </div>
        <div class="text-[11px] text-amber-800/90 dark:text-amber-300/90 mt-1.5 leading-relaxed">
          {{ breakdown }}
        </div>
        <div class="text-[11px] text-amber-800/90 dark:text-amber-300/90 mt-1.5 leading-relaxed">
          {{ $t('ownership.remove.ownsWarning') }}
        </div>
        <!-- ★The backup moment, placed where it actually happens. Transferring
             keeps the work inside this install; this is the copy that survives
             the install. Offered before the irreversible button, not after. -->
        <UButton
          color="gray"
          variant="outline"
          size="2xs"
          icon="i-heroicons-arrow-down-tray"
          class="mt-2.5 cursor-pointer"
          :loading="exporting"
          data-testid="remove-export"
          @click="downloadCopy"
        >
          {{ $t('ownership.remove.download') }}
        </UButton>
      </div>

      <template v-if="summary && summary.total > 0">
        <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mt-4 mb-1.5">
          {{ $t('ownership.remove.transferLabel') }}
        </label>
        <USelectMenu
          v-model="recipient"
          :options="recipientOptions"
          option-attribute="label"
          by="id"
          searchable
          :search-attributes="['label', 'email']"
          :placeholder="$t('ownership.remove.transferPlaceholder')"
          data-testid="remove-transfer-recipient"
        />

        <label class="flex items-start gap-2.5 mt-3 cursor-pointer">
          <UCheckbox v-model="doTransfer" />
          <span class="text-sm text-gray-800 dark:text-gray-200 leading-snug">
            {{ $t('ownership.remove.transferFirst') }}
            <span class="block text-[11px] text-gray-400 dark:text-gray-500">
              {{ $t('ownership.remove.transferFirstHint') }}
            </span>
          </span>
        </label>
        <!-- ★★★The half a transfer does NOT do. Same rule and same wording as
             the transfer dialog, shown here because this dialog performs the
             same transfer: only reports with a dashboard, a schedule or a share
             move; a report that is nothing but a chat thread stays with the
             person, because reports are gated `owner_only` as conversation
             privacy. Gated on the transfer actually being about to happen —
             with the checkbox off, `ownsWarning` above already says nothing
             moves at all.
             ★Both numbers or neither, and the second sentence so "stay with
             them" is never read as "were deleted". -->
        <div
          v-if="willTransfer && conversationSplit"
          class="mt-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 px-3 py-2.5"
          data-testid="remove-conversation-split"
        >
          <p class="text-xs text-gray-700 dark:text-gray-300 leading-relaxed">
            {{ $t('ownership.transfer.splitLine', conversationSplit) }}
          </p>
          <p class="text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed mt-1">
            {{ $t('ownership.transfer.splitHint') }}
          </p>
        </div>

        <!-- ★The same consequence the transfer dialog warns about, shown here
             because this dialog performs the same transfer. Gated on the
             transfer actually being about to happen — a warning about what a
             handover does is noise when nothing is being handed over. -->
        <div
          v-if="willTransfer && summary && summary.credential_bound_data_sources"
          class="mt-3 rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50/70 dark:bg-amber-500/10 px-3 py-2.5"
          data-testid="remove-credentials-warning"
        >
          <p class="text-xs text-amber-900 dark:text-amber-200 leading-relaxed">
            {{ $t('ownership.transfer.credentialsWarning', {
              count: summary.credential_bound_data_sources,
              name: recipient?.label || recipient?.email || '',
            }) }}
          </p>
        </div>
      </template>

      <p v-if="error" class="mt-3 text-xs text-red-600 dark:text-red-400">{{ error }}</p>

      <div class="flex justify-end gap-2 mt-5">
        <UButton color="gray" variant="ghost" :disabled="busy" @click="close">
          {{ $t('common.cancel') }}
        </UButton>
        <UButton
          color="red"
          :loading="busy"
          :disabled="willTransfer && !recipient"
          data-testid="remove-confirm"
          @click="confirm"
        >
          {{ willTransfer ? $t('ownership.remove.confirmWithTransfer') : $t('ownership.remove.confirm') }}
        </UButton>
      </div>
    </div>
  </UModal>
</template>

<script setup lang="ts">
import type { ContentSummary } from '~/composables/useOwnership'

const props = defineProps<{
  modelValue: boolean
  organizationId: string
  membershipId: string
  memberName: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'removed'): void
}>()

const { t } = useI18n()
const toast = useToast()
const { fetchMemberSummary, exportMemberContent, error } = useOwnership()

// ★Declared above every reader — the temporal-dead-zone shape that has already
// cost this fork a release.
const doTransfer = ref(true)
const busy = ref(false)
// ★A separate flag from `busy`. Building a bundle can take a while, and sharing
// one would disable Cancel and the removal button during a download — an admin
// who exports first would appear to have jammed the dialog.
const exporting = ref(false)
const summary = ref<ContentSummary | null>(null)
const recipient = ref<{ id: string; label: string; email: string } | null>(null)
const members = ref<{ id: string; label: string; email: string }[]>([])

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// ★Only ever true when there is something to move AND somebody to move it to.
// The transfer is an escape hatch, never a precondition: a pending invite, an
// account created by mistake, or someone who owns nothing must still be
// removable in one click.
const willTransfer = computed(
  () => Boolean(doTransfer.value && summary.value && summary.value.total > 0),
)

// ★The two halves of what the transfer will do, or `null` when there is nothing
// to say. One object rather than two computeds, so the template cannot render
// half of it. `conversation_reports` is a SUB-count of `reports`, so what moves
// is the difference; clamped at zero so a summary from an older backend (field
// absent → 0) reports the plain report count rather than a negative one.
// ★Reads `summary`, declared above.
const conversationSplit = computed(() => {
  const s = summary.value
  if (!s) return null
  const staying = s.conversation_reports || 0
  const moving = Math.max(0, (s.reports || 0) - staying)
  if (!moving && !staying) return null
  return { moving, staying }
})

const recipientOptions = computed(() => members.value.filter((m) => m.id !== currentTargetUserId.value))
const currentTargetUserId = ref('')

const breakdown = computed(() => {
  if (!summary.value) return ''
  const s = summary.value
  const parts: string[] = []
  if (s.reports) parts.push(t('ownership.remove.nReports', { count: s.reports }))
  if (s.artifacts) parts.push(t('ownership.remove.nDashboards', { count: s.artifacts }))
  if (s.scheduled_prompts) parts.push(t('ownership.remove.nTasks', { count: s.scheduled_prompts }))
  if (s.projects) parts.push(t('ownership.remove.nFolders', { count: s.projects }))
  // ★Same order as the backend summary, so the sentence reads the same way the
  // counts are computed. Each stays behind its own `if`: a zero must not render
  // an empty segment and leave a dangling separator.
  if (s.notes) parts.push(t('ownership.remove.nNotes', { count: s.notes }))
  if (s.instructions) parts.push(t('ownership.remove.nInstructions', { count: s.instructions }))
  if (s.prompts) parts.push(t('ownership.remove.nPrompts', { count: s.prompts }))
  if (s.data_sources) parts.push(t('ownership.remove.nAgents', { count: s.data_sources }))
  return parts.join(' · ')
})

const loadMembers = async () => {
  try {
    const res = await useMyFetch('/organization/members')
    if (res.data.value) {
      members.value = (res.data.value as any[]).map((u: any) => ({
        id: String(u.id),
        label: u.name || u.email,
        email: u.email,
      }))
    }
  } catch { /* empty picker renders its own state */ }
}

// ★★★`immediate: true` is load-bearing, and its absence was a real bug.
// The parent renders this component under `v-if="targetMember"` and sets the
// target and the open flag in the same tick, so by the time this watcher is
// created `open` is ALREADY true. A plain watcher only fires on a CHANGE, so it
// never ran: no counts, no member list, no request at all — and the dialog
// still rendered perfectly, just without the section that makes it useful.
// I first read that empty section as the endpoint 404ing against the old
// backend. It was not; nothing was ever asked.
//
// ★Safe here only because every ref it touches is declared above. An immediate
// watcher evaluating a const still in its temporal dead zone is the shape that
// cost this fork the 0.0.518.1 release.
watch(open, async (isOpen) => {
  if (!isOpen) return
  error.value = ''
  recipient.value = null
  summary.value = null
  doTransfer.value = true
  await Promise.all([
    loadMembers(),
    fetchMemberSummary(props.organizationId, props.membershipId).then((s) => {
      summary.value = s
    }),
  ])
}, { immediate: true })

const close = () => {
  if (!busy.value) open.value = false
}

const downloadCopy = async () => {
  exporting.value = true
  try {
    const ok = await exportMemberContent(props.organizationId, props.membershipId)
    if (ok) toast.add({ title: t('ownership.remove.downloadDone'), color: 'green' })
  } finally {
    exporting.value = false
  }
}

const confirm = async () => {
  busy.value = true
  error.value = ''
  try {
    // One request. The backend runs the transfer BEFORE its own removal body,
    // which is what keeps the person's scheduled tasks running without a gap —
    // doing it as two calls from here would reintroduce the window this is
    // meant to close.
    const query = willTransfer.value && recipient.value
      ? `?transfer_content_to=${encodeURIComponent(recipient.value.id)}`
      : ''
    const res = await useMyFetch(
      `/organizations/${props.organizationId}/members/${props.membershipId}${query}`,
      { method: 'DELETE' },
    )
    if (res.error.value) {
      error.value = (res.error.value as any)?.data?.detail || t('settings.members.failedToRemove')
      return
    }
    toast.add({
      title: willTransfer.value
        ? t('ownership.remove.doneWithTransfer', { name: recipient.value?.label || '' })
        : t('ownership.remove.done'),
      color: 'green',
    })
    emit('removed')
    open.value = false
  } finally {
    busy.value = false
  }
}
</script>
