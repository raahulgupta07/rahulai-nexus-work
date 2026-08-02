<template>
  <div>
      <!-- Full-page empty state (no tasks, no refreshes, no active search).
           ★ `refreshes.length` is part of the condition on purpose: this screen
           used to declare "Nothing scheduled yet" while a report refresh was
           registered with APScheduler and firing daily, because it only ever
           counted scheduled prompts. An empty state that is wrong is worse than
           no empty state — it sends people to re-create something that exists. -->
      <div v-if="!isLoading && tasks.length === 0 && refreshes.length === 0 && !searchTerm && statusFilter === 'all'" class="flex flex-col items-center justify-center text-center py-20 px-4">
        <img src="/assets/empty-states/empty-pond.png" alt="" class="w-full max-w-sm opacity-90 select-none pointer-events-none" />
        <h3 class="mt-2 text-sm font-medium text-gray-900 dark:text-white">{{ $t('scheduled.empty') }}</h3>
        <p class="mt-1 max-w-xs text-xs leading-relaxed text-gray-500 dark:text-gray-400">{{ $t('scheduled.emptyDescription') }}</p>
        <button
          @click="openNewTask"
          :disabled="creatingTask"
          class="mt-5 inline-flex items-center gap-1.5 h-8 px-3 rounded-md bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          <Spinner v-if="creatingTask" class="w-3 h-3 animate-spin" />
          <UIcon v-else name="heroicons-plus" class="w-3.5 h-3.5" />
          {{ creatingTask ? $t('scheduled.creating') : $t('scheduled.newTask') }}
        </button>
      </div>

      <template v-else>
      <div class="mb-5">
        <div class="flex items-center justify-between">
          <div class="text-xs text-gray-500 dark:text-gray-400">{{ $t('automations.scheduledDescription') }}</div>
          <button
            @click="openNewTask"
            :disabled="creatingTask"
            class="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-md transition-colors disabled:opacity-50"
          >
            <Spinner v-if="creatingTask" class="w-3 h-3 animate-spin" />
            <UIcon v-else name="heroicons-plus" class="w-3.5 h-3.5" />
            {{ creatingTask ? $t('scheduled.creating') : $t('scheduled.newTask') }}
          </button>
        </div>

        <div class="mt-3 flex items-center gap-2">
          <input v-model="searchTerm" type="text" :placeholder="$t('scheduled.searchPlaceholder')" class="w-full text-sm border rounded px-3 py-2 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-100 dark:placeholder-gray-500" />
        </div>

        <!-- Ownership scope. The list was silently `filter: 'my'`, so a member
             looking at an org with schedules saw an empty page and no hint that
             a filter was applied. "Everyone" is still visibility-scoped on the
             server — it widens the filter, not the permission. -->
        <div class="mt-3 flex items-center gap-2">
          <!-- Non-admins get the label, not the switch: listing other people's
               prompts is admin-only on the server (their prompt text is not
               org-public), so offering the option would only ever 403. The
               label still answers "why is this empty". -->
          <span v-if="!canSeeEveryone" class="text-[10px] text-gray-400 dark:text-gray-500 border border-gray-200 dark:border-gray-800 rounded-full px-2.5 py-1">{{ $t('scheduled.scopeMineOnly') }}</span>
          <div v-else class="flex gap-0.5 p-0.5 bg-gray-100 dark:bg-gray-800 rounded w-fit">
            <button
              v-for="s in scopeFilters"
              :key="s.value"
              class="px-2.5 py-1 text-[11px] rounded transition-colors"
              :class="scopeFilter === s.value ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-white shadow-sm font-medium' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
              @click="scopeFilter = s.value"
            >
              {{ s.label }}
            </button>
          </div>
        </div>

        <!-- Status filter: All | Active | Paused -->
        <div class="mt-3 flex gap-0.5 p-0.5 bg-gray-100 dark:bg-gray-800 rounded w-fit">
          <button
            v-for="f in statusFilters"
            :key="f.value"
            class="px-2.5 py-1 text-[11px] rounded transition-colors"
            :class="statusFilter === f.value ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-white shadow-sm font-medium' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'"
            @click="statusFilter = f.value"
          >
            {{ f.label }}
          </button>
        </div>
      </div>

      <!-- ── Report refreshes ──────────────────────────────────────────────
           The OTHER scheduling mechanism. "Schedule and rerun report" writes
           Report.cron_schedule; this tab used to read only scheduled_prompts,
           so these were invisible here no matter how many you created. They
           are not editable from this row — the report's own schedule dialog
           owns them — so the row links out instead of pretending otherwise. -->
      <div v-if="!isLoading && refreshes.length" class="mb-5">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('scheduled.refreshSection') }}</span>
          <span class="text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">{{ refreshes.length }}</span>
          <span class="text-[10px] text-gray-400 dark:text-gray-500">· {{ $t('scheduled.refreshSectionHint') }}</span>
        </div>
        <div class="space-y-2">
          <NuxtLink
            v-for="rf in refreshes"
            :key="rf.id"
            :to="`/reports/${rf.report_id}`"
            class="block border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-lg px-4 py-3 hover:shadow-md hover:border-gray-200 dark:hover:border-gray-700 transition-all"
          >
            <div class="flex items-center gap-3">
              <UIcon name="i-heroicons-arrow-path" class="w-4 h-4 shrink-0 text-teal-600 dark:text-teal-400" />
              <div class="min-w-0 flex-1">
                <div class="text-sm font-medium text-gray-900 dark:text-white truncate">{{ rf.title || $t('scheduled.untitledReport') }}</div>
                <div class="flex items-center gap-2 mt-0.5">
                  <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ getCronLabel(rf.cron_schedule) }}</span>
                  <template v-if="rf.next_run_at">
                    <span class="text-gray-300 dark:text-gray-600">·</span>
                    <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ $t('scheduled.nextRun', { time: formatRelativeTime(rf.next_run_at) }) }}</span>
                  </template>
                  <!-- Registered in the database but with no live job: it will
                       never fire, and a row that looks identical to a healthy
                       one is exactly how that stays unnoticed. -->
                  <template v-if="rf.orphaned">
                    <span class="text-gray-300 dark:text-gray-600">·</span>
                    <span class="inline-flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400"><UIcon name="i-heroicons-exclamation-triangle" class="w-3 h-3" />{{ $t('scheduled.notRegistered') }}</span>
                  </template>
                </div>
              </div>
              <span class="shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded bg-teal-50 dark:bg-teal-900/30 text-teal-700 dark:text-teal-300">{{ $t('scheduled.kindRefresh') }}</span>
              <span v-if="!rf.is_mine && rf.owner_name" class="shrink-0 text-[10px] text-gray-400 dark:text-gray-500">{{ $t('scheduled.by', { name: rf.owner_name }) }}</span>
            </div>
          </NuxtLink>
        </div>
        <div v-if="tasks.length" class="mt-5 mb-2 flex items-center gap-2">
          <span class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('scheduled.promptSection') }}</span>
          <span class="text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">{{ pagination.total }}</span>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="isLoading" class="text-xs text-gray-500 dark:text-gray-400 inline-flex items-center">
        <Spinner class="me-1" /> {{ $t('scheduled.loading') }}
      </div>

      <!-- No matches for the current search (page chrome stays visible).
           Suppressed when refreshes are listed above: "Nothing scheduled yet"
           under a list of schedules is the same false statement, indented. -->
      <div v-else-if="tasks.length === 0 && !refreshes.length" class="py-12 text-center text-xs text-gray-500 dark:text-gray-400">
        {{ $t('scheduled.empty') }}
      </div>

      <!-- Task cards -->
      <div v-else class="space-y-3">
        <div
          v-for="task in tasks"
          :key="task.id"
          class="border border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 rounded-lg p-4 hover:shadow-md hover:border-gray-200 dark:hover:border-gray-700 transition-all cursor-pointer"
          @click="openTask(task)"
        >
          <div class="group flex items-start justify-between gap-3">
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium text-gray-900 dark:text-white mb-1 line-clamp-2" :class="{ 'opacity-50': !task.is_active }">
                {{ task.title || task.prompt?.content || $t('scheduled.untitledTask') }}
              </div>
              <div class="flex items-center gap-2">
                <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ getCronLabel(task.cron_schedule) }}</span>
                <!-- A schedule whose last run died reads as healthy here unless
                     the row says otherwise — the same blind spot the failure
                     alert closes for the inbox. -->
                <span
                    v-if="task.last_run_at"
                    class="inline-flex items-center gap-1 text-[11px]"
                    :class="task.last_run_status === 'error' ? 'text-red-500' : 'text-gray-400 dark:text-gray-500'"
                    :data-testid="`task-last-run-${task.id}`"
                >
                    <span class="text-gray-300 dark:text-gray-600">&middot;</span>
                    <UIcon v-if="task.last_run_status === 'error'" name="i-heroicons-exclamation-triangle" class="w-3 h-3 shrink-0" />
                    {{ task.last_run_status === 'error'
                        ? $t('scheduled.lastRunFailed', { time: formatRelativeTime(task.last_run_at) })
                        : $t('scheduled.lastRun', { time: formatRelativeTime(task.last_run_at) }) }}
                </span>
              </div>
              <div class="flex items-center gap-3 mt-2">
                <NuxtLink
                  :to="`/reports/${task.report_id}`"
                  class="text-[11px] text-blue-500 hover:text-blue-600 flex items-center gap-1"
                  @click.stop
                >
                  <UIcon name="heroicons-chat-bubble-left-right" class="w-3 h-3" />
                  {{ task.report?.title || $t('scheduled.untitledReport') }}
                </NuxtLink>
                <span v-if="task.user_name" class="text-[11px] text-gray-400 dark:text-gray-500">{{ $t('scheduled.by', { name: task.user_name }) }}</span>
              </div>
            </div>
            <div class="shrink-0 flex items-center gap-2">
              <UTooltip :text="task.is_active ? $t('scheduled.pause') : $t('scheduled.resume')">
                <button
                  @click.stop="toggleActive(task)"
                  :disabled="togglingId === task.id"
                  class="relative inline-flex h-4 w-7 items-center rounded-full transition-colors disabled:opacity-50"
                  :class="task.is_active ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-700'"
                  :aria-pressed="task.is_active"
                >
                  <span class="inline-block h-3 w-3 rounded-full bg-white transition-transform" :class="task.is_active ? 'translate-x-3.5' : 'translate-x-0.5'" />
                </button>
              </UTooltip>
              <UTooltip :text="$t('scheduled.delete')">
                <button
                  @click.stop="deleteTask(task)"
                  :disabled="deletingId === task.id"
                  class="p-1 rounded text-gray-300 dark:text-gray-600 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-all disabled:opacity-50"
                >
                  <Spinner v-if="deletingId === task.id" class="w-3.5 h-3.5 animate-spin" />
                  <UIcon v-else name="heroicons-trash" class="w-3.5 h-3.5" />
                </button>
              </UTooltip>
            </div>
          </div>
        </div>
      </div>

      <!-- Results summary -->
      <div v-if="!isLoading && tasks.length > 0" class="mt-6 text-center text-[11px] text-gray-500 dark:text-gray-400">
        {{ $t(pagination.total === 1 ? 'scheduled.showingOne' : 'scheduled.showingMany', { shown: tasks.length, total: pagination.total }) }}
      </div>

      <!-- Load more -->
      <div v-if="pagination.has_next" class="mt-4 text-center">
        <button
          @click="loadMore"
          :disabled="isLoadingMore"
          class="text-xs px-3 py-1.5 rounded border border-gray-200 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50 text-gray-600 dark:text-gray-400 disabled:opacity-50"
        >
          <template v-if="isLoadingMore"><Spinner class="w-3 h-3 inline me-1" /> {{ $t('scheduled.loading') }}</template>
          <template v-else>{{ $t('scheduled.loadMore') }}</template>
        </button>
      </div>
      </template>

    <!-- Scheduled Prompt Modal -->
    <ScheduledPromptModal
      v-if="modalReportId"
      v-model="showModal"
      :report-id="modalReportId"
      :scheduled-prompt="editingTask"
      @saved="onTaskSaved"
    />
  </div>
</template>

<script setup lang="ts">
import Spinner from '~/components/Spinner.vue'
import ScheduledPromptModal from '~/components/ScheduledPromptModal.vue'

const toast = useToast()
const { t } = useI18n()
const { selectedAgentObjects } = useAgent()

const tasks = ref<any[]>([])
const isLoading = ref(true)
const isLoadingMore = ref(false)
const currentPage = ref(1)
const pagination = ref({ total: 0, page: 1, limit: 20, total_pages: 0, has_next: false, has_prev: false })
const searchTerm = ref('')

// Report refresh schedules — the second scheduling mechanism, fetched from its
// own endpoint. Kept as a separate list rather than merged into `tasks`: a
// refresh has no prompt, no pause flag and no delete of its own, so folding it
// into the task array would mean every task handler needing a kind check.
const refreshes = ref<any[]>([])

// Ownership scope. Previously hardcoded to 'my' in the fetch with nothing in
// the UI to say so.
type ScopeFilter = 'my' | 'all'
const scopeFilter = ref<ScopeFilter>('my')
const canSeeEveryone = computed(() => useCan('full_admin_access'))
const scopeFilters = computed(() => [
  { value: 'my' as const, label: t('scheduled.scopeMine') },
  { value: 'all' as const, label: t('scheduled.scopeEveryone') },
])

// Status filter tabs: All | Active | Paused
type StatusFilter = 'all' | 'active' | 'paused'
const statusFilter = ref<StatusFilter>('all')
const statusFilters = computed(() => [
  { value: 'all' as const, label: t('scheduled.filterAll') },
  { value: 'active' as const, label: t('scheduled.filterActive') },
  { value: 'paused' as const, label: t('scheduled.filterPaused') },
])

// Scheduled prompt modal (shared for create + edit)
const showModal = ref(false)
const modalReportId = ref<string | null>(null)
const editingTask = ref<any | null>(null)
const creatingTask = ref(false)
const deletingId = ref<string | null>(null)
const togglingId = ref<string | null>(null)

// Pause/resume in place. Optimistic: flip locally, revert on failure. A task
// that no longer matches the current status tab drops out of the list.
const toggleActive = async (task: any) => {
  if (togglingId.value) return
  togglingId.value = task.id
  const next = !task.is_active
  task.is_active = next
  try {
    const response = await useMyFetch(`/reports/${task.report_id}/scheduled-prompts/${task.id}`, {
      method: 'PUT',
      body: JSON.stringify({ is_active: next }),
    })
    if ((response as any).error?.value) throw new Error('Update failed')
    if (statusFilter.value !== 'all' && (statusFilter.value === 'active') !== next) {
      tasks.value = tasks.value.filter((t: any) => t.id !== task.id)
      pagination.value.total = Math.max(0, (pagination.value.total || 1) - 1)
    }
  } catch (error) {
    console.error('Error toggling scheduled task:', error)
    task.is_active = !next
    toast.add({ title: t('common.error'), description: t('scheduled.updateFailed'), color: 'red' })
  } finally {
    togglingId.value = null
  }
}

const openTask = (task: any) => {
  editingTask.value = task
  modalReportId.value = task.report_id
  showModal.value = true
}

const openNewTask = async () => {
  if (creatingTask.value) return
  creatingTask.value = true
  try {
    const dataSourceIds = selectedAgentObjects.value.map((a: any) => a.id)
    const response = await useMyFetch('/reports', {
      method: 'POST',
      body: JSON.stringify({
        title: t('scheduled.defaultTitle'),
        files: [],
        data_sources: dataSourceIds,
      }),
    })
    if ((response as any).error?.value) throw new Error('Report creation failed')
    const data = ((response as any).data?.value) as any
    editingTask.value = null
    modalReportId.value = data.id
    showModal.value = true
  } catch {
    toast.add({ title: t('common.error'), description: t('scheduled.createFailed'), color: 'red' })
  } finally {
    creatingTask.value = false
  }
}

const onTaskSaved = () => {
  showModal.value = false
  currentPage.value = 1
  fetchTasks(1, searchTerm.value)
}

const deleteTask = async (task: any) => {
  if (deletingId.value) return
  if (!confirm(t('scheduled.deleteConfirm'))) return
  deletingId.value = task.id
  try {
    const response = await useMyFetch(`/reports/${task.report_id}/scheduled-prompts/${task.id}`, {
      method: 'DELETE',
    })
    if ((response as any).error?.value) throw new Error('Delete failed')
    // Drop it locally so the list updates without a full refetch.
    tasks.value = tasks.value.filter((t: any) => t.id !== task.id)
    pagination.value.total = Math.max(0, (pagination.value.total || 1) - 1)
    toast.add({ title: t('scheduled.toastDeleted'), color: 'green' })
  } catch (error) {
    console.error('Error deleting scheduled task:', error)
    toast.add({ title: t('common.error'), description: t('scheduled.deleteFailed'), color: 'red' })
  } finally {
    deletingId.value = null
  }
}

const fetchTasks = async (page: number = 1, search: string = '') => {
  if (page === 1) isLoading.value = true
  try {
    const response = await useMyFetch('/scheduled-prompts', {
      method: 'GET',
      query: {
        page,
        limit: pagination.value.limit,
        // 'shared' is the API's word for "not mine"; the scope toggle offers
        // mine-or-everyone, so 'all' simply omits the filter.
        filter: scopeFilter.value === 'my' ? 'my' : undefined,
        search: search?.trim() || undefined,
        status: statusFilter.value,
      },
    })
    if (response.status.value === 'success' && response.data.value) {
      const data = response.data.value as any
      if (page === 1) {
        tasks.value = data.scheduled_prompts
      } else {
        tasks.value = [...tasks.value, ...data.scheduled_prompts]
      }
      pagination.value = data.meta
    } else {
      throw new Error('Could not fetch scheduled tasks')
    }
  } catch (error) {
    console.error('Error fetching scheduled tasks:', error)
    if (page === 1) tasks.value = []
  } finally {
    isLoading.value = false
    isLoadingMore.value = false
  }
}

const loadMore = async () => {
  isLoadingMore.value = true
  currentPage.value++
  await fetchTasks(currentPage.value, searchTerm.value)
}

const { relativeTime: formatRelativeTime } = useRelativeTime()

const { getCronLabel } = useCronLabel()

// Report refresh schedules. Unpaginated and unsearched — one row per report
// that has a schedule, which is a small set, and the search box above is wired
// to the prompt query. Failing quietly is deliberate: a refresh list that
// errors must not take down the prompt list beside it.
const fetchRefreshes = async () => {
  try {
    const { data } = await useMyFetch('/report-refreshes', {
      method: 'GET',
      query: { filter: scopeFilter.value },
    })
    refreshes.value = (data.value as any)?.refreshes || []
  } catch {
    refreshes.value = []
  }
}

let _searchTimer: any = null
watch(searchTerm, () => {
  if (_searchTimer) clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => {
    currentPage.value = 1
    fetchTasks(1, searchTerm.value)
  }, 300)
})

watch(statusFilter, () => {
  currentPage.value = 1
  fetchTasks(1, searchTerm.value)
})

watch(scopeFilter, () => {
  currentPage.value = 1
  fetchTasks(1, searchTerm.value)
  fetchRefreshes()
})

onMounted(async () => {
  await nextTick()
  await Promise.all([fetchTasks(1, ''), fetchRefreshes()])
})
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
