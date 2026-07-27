<template>
  <!-- Inline progress strip: slides up from the bottom of the Tables panel via a
       max-height transition. No Teleport, no scrim, no side window. -->
  <div
    class="overflow-hidden transition-[max-height] duration-300 ease-out border-t border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-800/40"
    :style="{ maxHeight: modelValue ? '96px' : '0px' }"
  >
    <!-- Thin progress bar: indeterminate during the AI stage, step-proportional
         otherwise. -->
    <div class="relative h-1 overflow-hidden bg-blue-500/15 dark:bg-blue-400/15">
      <i
        v-if="isIndeterminate"
        class="absolute inset-y-0 left-0 w-1/3 rounded-r bg-gradient-to-r from-blue-500 to-blue-600 lp-indet"
      />
      <i
        v-else
        class="absolute inset-y-0 left-0 rounded-r bg-gradient-to-r from-blue-500 to-blue-600 transition-[width] duration-300 ease-out"
        :style="{ width: barPct + '%' }"
      />
    </div>

    <!-- Info row -->
    <div class="flex flex-wrap items-center justify-between gap-3 px-4 py-2 text-xs">
      <div class="flex min-w-0 items-center gap-2.5">
        <!-- Step pips -->
        <span class="flex gap-1.5">
          <span
            v-for="(s, i) in STAGES"
            :key="s.key"
            class="h-[5px] w-4 rounded transition-colors duration-300"
            :class="pipClass(i)"
          />
        </span>
        <span
          class="font-semibold truncate"
          :class="isDone ? 'text-green-600 dark:text-green-400' : 'text-gray-800 dark:text-gray-100'"
        >{{ stageLabel }}</span>
        <span class="font-mono text-[11px] text-gray-400 dark:text-gray-500 truncate">{{ subLabel }}</span>
      </div>
      <div class="flex items-center gap-3 whitespace-nowrap font-mono text-[11px] text-gray-400 dark:text-gray-500">
        <span v-if="!isDone && !isError">step <b class="font-semibold text-gray-600 dark:text-gray-300">{{ displayStep }} / {{ total }}</b></span>
        <span>elapsed <b class="font-semibold text-gray-600 dark:text-gray-300">{{ elapsedLabel }}</b></span>
        <span v-if="secondsLeft !== null && !isDone && !isError">~<b class="font-semibold text-gray-600 dark:text-gray-300">{{ secondsLeft }}s</b></span>
        <button
          type="button"
          class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-sm leading-none"
          @click="close"
        >&times;</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  dsId: string
  modelValue: boolean
}>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

type StageKey = 'reading_tables' | 'analyzing' | 'generating_overview' | 'grounding_publishing'

// Row/pip definitions — order == step 1..4. Mirrors the backend stage → step map.
const STAGES: { key: StageKey; name: string }[] = [
  { key: 'reading_tables', name: 'Read active tables' },
  { key: 'analyzing', name: 'Analyze schema & relationships' },
  { key: 'generating_overview', name: 'Generate overview (AI)' },
  { key: 'grounding_publishing', name: 'Ground references & publish' },
]

const STAGE_INDEX: Record<StageKey, number> = {
  reading_tables: 0,
  analyzing: 1,
  generating_overview: 2,
  grounding_publishing: 3,
}

type LearnStatus = {
  status: 'idle' | 'running' | 'done' | 'error'
  stage: StageKey | null
  step: number
  total: number
  tables: number
  columns: number
  elapsed_ms: number
  error: string | null
}

const status = ref<LearnStatus | null>(null)
let pollTimer: any = null

const isDone = computed(() => status.value?.status === 'done')
const isError = computed(() => status.value?.status === 'error')
const total = computed(() => status.value?.total || 4)

// Current active stage index (0..3). Falls back to the reported step.
const activeIndex = computed(() => {
  const s = status.value
  if (!s) return 0
  if (s.status === 'done') return STAGES.length
  if (s.stage && s.stage in STAGE_INDEX) return STAGE_INDEX[s.stage]
  return Math.max(0, (s.step || 1) - 1)
})

const displayStep = computed(() => {
  const s = status.value
  if (!s || s.status === 'idle') return 0
  if (s.status === 'done') return total.value
  return Math.min(total.value, Math.max(1, s.step || activeIndex.value + 1))
})

// The AI generate-overview stage has no true %, so the bar goes indeterminate.
const isIndeterminate = computed(() =>
  !isDone.value && !isError.value && status.value?.stage === 'generating_overview')

const barPct = computed(() => {
  if (isDone.value) return 100
  if (isError.value) return Math.round((activeIndex.value / total.value) * 100)
  return Math.round(((activeIndex.value + 1) / total.value) * 100)
})

function pipClass(i: number): string {
  if (isDone.value) return 'bg-green-500'
  if (i < activeIndex.value) return 'bg-green-500'
  if (i === activeIndex.value) return isError.value ? 'bg-red-500' : 'bg-blue-500'
  return 'bg-gray-300 dark:bg-gray-600'
}

const stageLabel = computed(() => {
  if (isDone.value) return 'Agent learned'
  if (isError.value) return 'Learning failed'
  const s = status.value
  if (s?.stage && s.stage in STAGE_INDEX) return STAGES[STAGE_INDEX[s.stage]].name
  return STAGES[activeIndex.value]?.name || 'Learning agent'
})

const subLabel = computed(() => {
  const s = status.value
  if (!s) return ''
  if (isDone.value) return 'overview ready'
  if (isError.value) return s.error || 'please try again'
  const t = s.tables ?? 0
  const c = s.columns ?? 0
  switch (s.stage) {
    case 'reading_tables': return `${t} tables · ${c} columns`
    case 'analyzing': return 'keys, joins, entity roles'
    case 'generating_overview': return `writing overview from ${t} tables…`
    case 'grounding_publishing': return 'link @tables · set overview'
    default: return ''
  }
})

function fmt(ms: number): string {
  const s = Math.max(0, Math.floor((ms || 0) / 1000))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}
const elapsedLabel = computed(() => fmt(status.value?.elapsed_ms || 0))

// Rough "time left" heuristic: the AI stage dominates; estimate from the number
// of remaining stages. Best-effort only (there is no true ETA from the backend).
const secondsLeft = computed<number | null>(() => {
  const s = status.value
  if (!s || s.status !== 'running') return null
  const perStage = [1, 2, 9, 2] // rough seconds per stage
  let rem = 0
  for (let i = activeIndex.value; i < perStage.length; i++) rem += perStage[i]
  return rem
})

// --- Polling (unchanged contract: GET learn-status every 1s) ----------------
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function pollStatus() {
  if (!props.dsId) return
  try {
    const { data, error } = await useMyFetch(`/data_sources/${props.dsId}/learn-status`, { method: 'GET' })
    if (error.value) throw error.value
    const p = data.value as LearnStatus | null
    if (p) status.value = p
    if (p && (p.status === 'done' || p.status === 'error')) {
      stopPolling()
      if (p.status === 'done') {
        // Let the "Agent learned" state read for a beat, then collapse the strip.
        setTimeout(() => { if (props.modelValue) close() }, 2500)
      }
    }
  } catch (e) {
    // Transient blip — keep polling; a terminal state comes from the endpoint.
  }
}

function startPolling() {
  stopPolling()
  // Reset local view for a fresh run.
  status.value = { status: 'running', stage: 'reading_tables', step: 1, total: 4, tables: 0, columns: 0, elapsed_ms: 0, error: null }
  pollStatus()
  pollTimer = setInterval(pollStatus, 1000)
}

function close() {
  emit('update:modelValue', false)
}

watch(() => props.modelValue, (visible) => {
  if (visible) startPolling()
  else stopPolling()
})

onMounted(() => {
  if (props.modelValue) startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
})
</script>

<style scoped>
@keyframes lp-indet-kf {
  0% { left: -32%; }
  100% { left: 100%; }
}
.lp-indet { animation: lp-indet-kf 1.2s ease-in-out infinite; }
</style>
