<template>
    <UModal v-model="isOpen" :ui="{ width: 'sm:max-w-2xl' }">
        <UCard :ui="{ body: { padding: 'px-5 py-4 sm:p-5' }, header: { padding: 'px-5 py-3 sm:px-5 sm:py-3' }, footer: { padding: 'px-5 py-3 sm:px-5 sm:py-3' } }">
            <template #header>
                <div class="flex items-center justify-between">
                    <div class="min-w-0">
                        <h3 class="text-sm font-semibold text-gray-900 dark:text-white">{{ $t('refreshSchedule.title') }}</h3>
                        <NuxtLink
                            v-if="reportId"
                            :to="`/reports/${reportId}`"
                            class="mt-0.5 inline-flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-600"
                            @click="isOpen = false"
                        >
                            <Icon name="heroicons:chat-bubble-left-right" class="w-3 h-3" />
                            {{ reportTitle }}
                        </NuxtLink>
                    </div>
                    <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark-20-solid" size="xs" @click="isOpen = false" />
                </div>
            </template>

            <!-- ── Refresh mode ──────────────────────────────────────────────
                 One question — when does this report's data rerun? — even though
                 the API stores `cron_schedule` and `refresh_on_view` as two
                 independent fields. useRefreshMode owns that mapping in both
                 directions; never set either field directly from here, or a
                 report ends up with a schedule AND on-open refresh and the modal
                 shows only one of them. -->
            <div class="text-xs text-gray-500 dark:text-gray-400 mb-1.5">{{ $t('refreshSchedule.refreshData') }}</div>
            <div class="flex gap-0.5 p-0.5 bg-gray-100 dark:bg-gray-800 rounded w-fit" data-testid="refresh-mode">
                <button
                    v-for="opt in refreshModeOptions"
                    :key="opt.value"
                    type="button"
                    class="px-2.5 py-0.5 text-[11px] rounded transition-colors"
                    :class="refreshMode === opt.value ? 'bg-white dark:bg-gray-900 text-gray-900 dark:text-white shadow-sm font-medium' : 'text-gray-400 hover:text-gray-600'"
                    :data-testid="`refresh-mode-${opt.value}`"
                    @click="selectMode(opt.value)"
                >
                    {{ opt.label }}
                </button>
            </div>
            <p class="mt-1 text-[11px] text-gray-400" data-testid="refresh-mode-help">{{ modeHelp }}</p>

            <!-- ── Cron builder (recurring only) ─────────────────────────── -->
            <div v-if="scheduleEnabled" class="mt-3">
                <div class="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400 flex-wrap">
                    <!-- One lead-in, not two: `refreshSchedule.runs` carries the
                         whole "Runs every" phrase so translators can order it
                         naturally instead of concatenating two fragments. -->
                    <span>{{ $t('refreshSchedule.runs') }}</span>
                    <template v-if="recurInterval === 'minutes' || recurInterval === 'hours'">
                        <input v-model.number="recurEveryN" type="number" min="1" :max="recurInterval === 'minutes' ? 59 : 23"
                            class="w-12 rounded border border-gray-200 dark:border-gray-700 px-1 py-1 text-xs text-center" />
                    </template>
                    <select v-model="recurInterval" class="rounded border border-gray-200 dark:border-gray-700 px-1.5 py-1 text-xs">
                        <option value="minutes">{{ $t('scheduledPrompt.intervalMinutes') }}</option>
                        <option value="hours">{{ $t('scheduledPrompt.intervalHours') }}</option>
                        <option value="day">{{ $t('scheduledPrompt.intervalDay') }}</option>
                        <option value="weekdays">{{ $t('scheduledPrompt.intervalWeekdays') }}</option>
                        <option value="week">{{ $t('scheduledPrompt.intervalWeek') }}</option>
                        <option value="month">{{ $t('scheduledPrompt.intervalMonth') }}</option>
                    </select>
                    <template v-if="recurInterval === 'day' || recurInterval === 'weekdays' || recurInterval === 'week' || recurInterval === 'month'">
                        <span>{{ $t('refreshSchedule.at') }}</span>
                        <select v-model="recurHour" class="rounded border border-gray-200 dark:border-gray-700 px-1.5 py-1 text-xs">
                            <option v-for="h in 24" :key="h - 1" :value="h - 1">{{ String(h - 1).padStart(2, '0') }}:00</option>
                        </select>
                    </template>
                    <template v-if="recurInterval === 'week'">
                        <span>{{ $t('scheduledPrompt.on') }}</span>
                        <div class="flex items-center gap-1">
                            <button
                                v-for="d in weekdays"
                                :key="d.value"
                                type="button"
                                @click="toggleRecurDay(d.value)"
                                :title="d.label"
                                :aria-pressed="recurDays.includes(d.value)"
                                class="flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-medium border transition-colors"
                                :class="recurDays.includes(d.value)
                                    ? 'bg-blue-500 text-white border-blue-500'
                                    : 'bg-white dark:bg-gray-900 text-gray-500 dark:text-gray-400 border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'"
                            >
                                {{ d.short }}
                            </button>
                        </div>
                    </template>
                    <template v-if="recurInterval === 'month'">
                        <span>{{ $t('scheduledPrompt.onDay') }}</span>
                        <select v-model="recurDayOfMonth" class="rounded border border-gray-200 dark:border-gray-700 px-1.5 py-1 text-xs">
                            <option v-for="d in 28" :key="d" :value="d">{{ d }}</option>
                        </select>
                    </template>
                </div>
                <p v-if="scheduleLabel" class="mt-1 text-[11px] text-gray-400" data-testid="cron-label">{{ scheduleLabel }}</p>
            </div>

            <!-- ── Active toggle (recurring only) ─────────────────────────
                 Pausing is NOT "Off": off clears the cron and loses the time the
                 user picked, which is exactly why this control exists. The help
                 line has to say so — a bare toggle labelled "Active" is
                 indistinguishable from the Off mode above it. -->
            <div v-if="scheduleEnabled" class="mt-4 border-t border-gray-100 dark:border-gray-800 pt-3">
                <div class="flex items-center justify-between gap-3">
                    <span class="text-xs text-gray-500 dark:text-gray-400">{{ $t('refreshSchedule.active') }}</span>
                    <button
                        type="button"
                        data-testid="refresh-active"
                        @click="isActive = !isActive"
                        class="relative inline-flex h-4 w-7 items-center rounded-full transition-colors"
                        :class="isActive ? 'bg-blue-500' : 'bg-gray-300 dark:bg-gray-700'"
                        :aria-pressed="isActive"
                    >
                        <span class="inline-block h-3 w-3 rounded-full bg-white transition-transform" :class="isActive ? 'translate-x-3.5' : 'translate-x-0.5'" />
                    </button>
                </div>
                <p class="mt-1 text-[11px] text-gray-400" data-testid="refresh-active-help">{{ $t('refreshSchedule.activeHelp') }}</p>

                <!-- A paused schedule has no next fire time; showing the stale
                     one would contradict the toggle sitting right above it. -->
                <div v-if="isActive && nextRunLabel" class="mt-2 flex items-start gap-2">
                    <span class="w-20 shrink-0 text-[11px] text-gray-400">{{ $t('refreshSchedule.nextRun') }}</span>
                    <span class="text-[11px] text-gray-600 dark:text-gray-300" data-testid="refresh-next-run">{{ nextRunLabel }}</span>
                </div>
            </div>

            <!-- ── Email recipients (recurring only, and only with SMTP) ──
                 On-open refreshes fire on a visitor's request, not on a run we
                 own, so there is nothing to notify anyone about. -->
            <div v-if="smtpEnabled && scheduleEnabled" class="border-t border-gray-100 dark:border-gray-800 pt-3 mt-3">
                <div class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 mb-1.5">
                    <Icon name="heroicons:envelope" class="w-3 h-3" />
                    {{ $t('refreshSchedule.emailTo') }}
                </div>
                <div class="flex flex-wrap items-center gap-1 border border-gray-200 dark:border-gray-700 rounded px-2 py-1 min-h-[30px] focus-within:ring-1 focus-within:ring-blue-500 focus-within:border-blue-500 bg-white dark:bg-gray-900">
                    <span v-for="(sub, idx) in subscribers" :key="idx"
                        class="inline-flex items-center gap-0.5 bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-[11px] px-1.5 py-0.5 rounded-full">
                        {{ sub.type === 'user' ? getMemberName(sub.id) : sub.address }}
                        <button @click="removeSubscriber(idx)" class="hover:text-red-500 outline-none">
                            <Icon name="heroicons:x-mark" class="w-2.5 h-2.5" />
                        </button>
                    </span>
                    <div class="relative flex-1 min-w-[120px]">
                        <input ref="inputRef" v-model="inputValue" type="text"
                            class="w-full border-none outline-none text-xs bg-transparent p-0"
                            :placeholder="$t('refreshSchedule.addSomeone')"
                            @keydown.enter.prevent="handleEnter"
                            @keydown.,.prevent="handleComma"
                            @keydown.backspace="handleBackspace"
                            @input="onMemberInput"
                            @focus="showMemberDropdown = true"
                            @blur="onBlur" />
                        <div v-if="showMemberDropdown && filteredMembers.length > 0"
                            class="absolute start-0 top-full mt-1 w-56 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded shadow-lg z-50 max-h-32 overflow-y-auto">
                            <button v-for="member in filteredMembers" :key="member.id"
                                class="w-full text-start px-2 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 flex flex-col"
                                @mousedown.prevent="addMember(member)">
                                <span class="text-gray-900 dark:text-white">{{ member.name || member.email }}</span>
                                <span v-if="member.name" class="text-[10px] text-gray-400">{{ member.email }}</span>
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <template #footer>
                <div class="flex items-center justify-between gap-2">
                    <!-- Third state, and the only irreversible one: the row leaves
                         this list and the saved time is gone. Kept visually apart
                         from both the mode picker and the pause toggle. -->
                    <UButton
                        color="red"
                        variant="ghost"
                        size="xs"
                        icon="i-heroicons-trash"
                        data-testid="refresh-remove"
                        :loading="isRemoving"
                        :disabled="isSaving"
                        @click="removeSchedule"
                    >{{ $t('refreshSchedule.remove') }}</UButton>
                    <div class="flex justify-end gap-2">
                        <UButton color="gray" variant="ghost" size="xs" :disabled="isSaving || isRemoving" @click="isOpen = false">
                            {{ $t('scheduledPrompt.cancel') }}
                        </UButton>
                        <UButton color="blue" size="xs" data-testid="refresh-save" :loading="isSaving" :disabled="isRemoving" @click="save">
                            {{ $t('scheduledPrompt.update') }}
                        </UButton>
                    </div>
                </div>
            </template>
        </UCard>
    </UModal>
</template>

<script lang="ts" setup>
/**
 * Edit a REPORT REFRESH from Automations → Scheduled.
 *
 * Same settings as the report page's CronModal, plus two things that list can
 * offer and the report page cannot: pausing a schedule without losing it
 * (`is_active`), and removing it outright.
 */
import { buildRecurringCron, parseRecurringCron, type RecurInterval } from '@/composables/useScheduleBuilder'
import { refreshModeFromReport, refreshModeSettings, type RefreshMode } from '@/composables/useRefreshMode'

const props = defineProps<{
    reportId: string
    refresh?: any
}>()

const emit = defineEmits(['saved', 'removed'])

const isOpen = defineModel<boolean>({ default: false })

const { t } = useI18n()
const toast = useToast()
const { smtpEnabled } = useAppSettings()
const { getCronLabel } = useCronLabel()
// Handles naive-UTC strings and renders in the org's timezone.
const { formatDateTime } = useFormatDate()

const isSaving = ref(false)
const isRemoving = ref(false)

const reportTitle = computed(() => props.refresh?.title || t('refreshSchedule.title'))

// ── Refresh mode ───────────────────────────────────────────────────────────
// The listing endpoint only returns reports that HAVE a cron, so a row opened
// from here is normally 'recurring'; the composable is still the only thing
// that decides, so a row that ever gains `refresh_on_view` resolves the same
// way the report page resolves it.
const refreshMode = ref<RefreshMode>(refreshModeFromReport(props.refresh))

const refreshModeOptions = computed(() => [
    { value: 'off' as const, label: t('refreshSchedule.modeOff') },
    { value: 'recurring' as const, label: t('refreshSchedule.modeRecurring') },
    { value: 'on_open' as const, label: t('refreshSchedule.modeOnOpen') },
])

const scheduleEnabled = computed(() => refreshMode.value === 'recurring')

const modeHelp = computed(() => {
    if (refreshMode.value === 'off') return t('refreshSchedule.modeOffHelp')
    if (refreshMode.value === 'on_open') return t('refreshSchedule.modeOnOpenHelp')
    return t('refreshSchedule.modeRecurringHelp')
})

// The modes are exclusive by construction: one ref holds the answer, and
// `refreshModeSettings` writes BOTH API fields on every save, so picking one
// mode always turns the other off. Nothing here writes `cron_expression` or
// `refresh_on_view` on its own.
function selectMode(mode: RefreshMode) {
    refreshMode.value = mode
}

// ── Cron builder ───────────────────────────────────────────────────────────
const recurInterval = ref<RecurInterval>('day')
const recurEveryN = ref(15)
const recurHour = ref(8)
const recurDays = ref<number[]>([1])
const recurDayOfMonth = ref(1)

const isActive = ref<boolean>(props.refresh?.is_active ?? true)

const weekdays = computed(() => [
    { value: 0, label: t('scheduledPrompt.dowSun'), short: t('scheduledPrompt.dowSunShort') },
    { value: 1, label: t('scheduledPrompt.dowMon'), short: t('scheduledPrompt.dowMonShort') },
    { value: 2, label: t('scheduledPrompt.dowTue'), short: t('scheduledPrompt.dowTueShort') },
    { value: 3, label: t('scheduledPrompt.dowWed'), short: t('scheduledPrompt.dowWedShort') },
    { value: 4, label: t('scheduledPrompt.dowThu'), short: t('scheduledPrompt.dowThuShort') },
    { value: 5, label: t('scheduledPrompt.dowFri'), short: t('scheduledPrompt.dowFriShort') },
    { value: 6, label: t('scheduledPrompt.dowSat'), short: t('scheduledPrompt.dowSatShort') },
])

function toggleRecurDay(value: number) {
    const idx = recurDays.value.indexOf(value)
    if (idx === -1) {
        recurDays.value = [...recurDays.value, value].sort((a, b) => a - b)
    } else if (recurDays.value.length > 1) {
        // Keep at least one day selected so the cron stays valid.
        recurDays.value = recurDays.value.filter((d) => d !== value)
    }
}

function currentCron(): string {
    return buildRecurringCron({
        interval: recurInterval.value,
        everyN: recurEveryN.value,
        hour: recurHour.value,
        days: recurDays.value,
        dayOfMonth: recurDayOfMonth.value,
    })
}

const scheduleLabel = computed(() => getCronLabel(currentCron()))

const nextRunLabel = computed(() => {
    const next = props.refresh?.next_run_at
    return next ? formatDateTime(next) : ''
})

// ── Subscribers ────────────────────────────────────────────────────────────
type Subscriber = { type: 'user'; id: string } | { type: 'email'; address: string }

const subscribers = ref<Subscriber[]>(
    (props.refresh?.notification_subscribers || []).map((s: any) => ({ ...s }))
)

const inputRef = ref<HTMLInputElement | null>(null)
const inputValue = ref('')
const showMemberDropdown = ref(false)

const members = ref<{ id: string; name: string; email: string }[]>([])
const fetchMembers = async () => {
    try {
        const res = await useMyFetch('/organization/members')
        if (res.data.value) {
            members.value = (res.data.value as any[]).map((u: any) => ({
                id: u.id,
                name: u.name || '',
                email: u.email,
            }))
        }
    } catch {}
}
fetchMembers()

const getMemberName = (userId: string | undefined) => {
    if (!userId) return t('scheduledPrompt.unknownMember')
    const m = members.value.find((m) => m.id === userId)
    return m ? (m.name || m.email) : userId
}

const subscriberEmails = computed(() => {
    return subscribers.value.map((s) => {
        if (s.type === 'email') return s.address
        const m = members.value.find((m) => m.id === (s as any).id)
        return m?.email
    })
})

const filteredMembers = computed(() => {
    const q = inputValue.value.toLowerCase().trim()
    if (!q) return []
    return members.value.filter(
        (m) =>
            !subscriberEmails.value.includes(m.email) &&
            (m.email.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
    ).slice(0, 5)
})

const isValidEmail = (email: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)

const addEmail = (email: string) => {
    const clean = email.trim().toLowerCase()
    if (clean && isValidEmail(clean) && !subscriberEmails.value.includes(clean)) {
        subscribers.value.push({ type: 'email', address: clean })
        inputValue.value = ''
    }
}

const addMember = (member: { id: string; name: string; email: string }) => {
    if (!subscribers.value.some((s) => s.type === 'user' && (s as any).id === member.id)) {
        subscribers.value.push({ type: 'user', id: member.id })
    }
    inputValue.value = ''
    showMemberDropdown.value = false
}

const removeSubscriber = (idx: number) => {
    subscribers.value.splice(idx, 1)
}

const handleEnter = () => {
    if (filteredMembers.value.length > 0) {
        addMember(filteredMembers.value[0])
    } else {
        addEmail(inputValue.value)
    }
}

const handleComma = () => {
    addEmail(inputValue.value)
}

const handleBackspace = () => {
    if (!inputValue.value && subscribers.value.length > 0) {
        subscribers.value.pop()
    }
}

const onMemberInput = () => {
    showMemberDropdown.value = true
}

const onBlur = () => {
    setTimeout(() => {
        showMemberDropdown.value = false
        if (inputValue.value && isValidEmail(inputValue.value)) {
            addEmail(inputValue.value)
        }
    }, 200)
}

// ── Hydration ──────────────────────────────────────────────────────────────
// Declared after every ref it writes: a forward reference from an `immediate`
// watch reads the binding in its temporal dead zone and takes the whole page
// down at setup, so the watches live at the bottom of this file.
function hydrateFromRefresh(row: any) {
    refreshMode.value = refreshModeFromReport(row)
    isActive.value = row?.is_active ?? true
    subscribers.value = (row?.notification_subscribers || []).map((s: any) => ({ ...s }))

    if (row?.cron_schedule) {
        const patch = parseRecurringCron(row.cron_schedule)
        if (patch) {
            if (patch.interval !== undefined) recurInterval.value = patch.interval
            if (patch.everyN !== undefined) recurEveryN.value = patch.everyN
            if (patch.hour !== undefined) recurHour.value = patch.hour
            if (patch.days !== undefined) recurDays.value = patch.days
            if (patch.dayOfMonth !== undefined) recurDayOfMonth.value = patch.dayOfMonth
        }
        return
    }
    // No cron to read: fall back to the same defaults the builder ships with,
    // so a reopened modal never shows the previous row's time.
    recurInterval.value = 'day'
    recurEveryN.value = 15
    recurHour.value = 8
    recurDays.value = [1]
    recurDayOfMonth.value = 1
}

// ── Save / remove ──────────────────────────────────────────────────────────
function scheduleBody() {
    return {
        // Both API fields, always — that is what makes the three modes
        // exclusive. Omitting `refresh_on_view` would leave it set.
        ...refreshModeSettings(refreshMode.value, currentCron()),
        notification_subscribers: scheduleEnabled.value && subscribers.value.length > 0 ? subscribers.value : null,
        is_active: isActive.value,
    }
}

async function save() {
    if (isSaving.value || isRemoving.value) return
    isSaving.value = true
    try {
        const res = await useMyFetch(`/reports/${props.reportId}/schedule`, {
            method: 'POST',
            body: scheduleBody(),
        })
        if (!res.data.value) throw new Error('save failed')
        isOpen.value = false
        // The list owns the row; hand it what we just saved so it can patch in
        // place instead of waiting for a refetch.
        emit('saved', {
            report_id: props.reportId,
            cron_schedule: refreshMode.value === 'recurring' ? currentCron() : null,
            is_active: isActive.value,
        })
    } catch {
        toast.add({ title: t('scheduledPrompt.toastError'), color: 'red', description: t('refreshSchedule.saveFailed') })
    } finally {
        isSaving.value = false
    }
}

// Removing is the same write as saving "Off" — the schedule endpoint is the
// only way to clear a cron — but it is a different act: it is immediate,
// discards the unsaved edits above, and the row disappears from the list. The
// confirm is what separates it from a mistaken click on Off.
async function removeSchedule() {
    if (isSaving.value || isRemoving.value) return
    if (!confirm(t('refreshSchedule.removeConfirm'))) return
    isRemoving.value = true
    try {
        const res = await useMyFetch(`/reports/${props.reportId}/schedule`, {
            method: 'POST',
            body: {
                ...refreshModeSettings('off', ''),
                notification_subscribers: null,
                // Leave the flag in its default state: a schedule created later
                // must not inherit "paused" from the one just removed.
                is_active: true,
            },
        })
        if (!res.data.value) throw new Error('remove failed')
        isOpen.value = false
        emit('removed', props.reportId)
    } catch {
        toast.add({ title: t('scheduledPrompt.toastError'), color: 'red', description: t('refreshSchedule.saveFailed') })
    } finally {
        isRemoving.value = false
    }
}

// ── Watches (last: every ref and function above is already bound) ──────────
// The list keeps one modal instance and swaps the row into it, so re-hydrating
// on open is what keeps the form from showing the previously edited schedule.
watch(isOpen, (open) => {
    if (open) hydrateFromRefresh(props.refresh)
}, { immediate: true })

watch(() => props.refresh, (row) => {
    if (isOpen.value) hydrateFromRefresh(row)
})
</script>
