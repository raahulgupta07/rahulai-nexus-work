<template>
  <div class="space-y-5">
    <!-- Uploaded files -->
    <section>
      <div class="flex items-center justify-between mb-2">
        <div>
          <h3 class="text-sm font-semibold text-gray-800 dark:text-gray-200">Uploaded</h3>
          <p class="text-xs text-gray-500 dark:text-gray-400">Files attached directly to this agent.</p>
        </div>
        <input ref="fileInput" type="file" class="hidden" multiple @change="onFileInput" />
        <button v-if="canUpdate" :disabled="uploading" @click="triggerUpload"
                class="text-xs px-3 py-1.5 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
          {{ uploading ? 'Uploading…' : '+ Upload files' }}
        </button>
      </div>
      <div v-if="files.length === 0" class="text-xs text-gray-400 dark:text-gray-500 py-2">No uploaded files yet.</div>
      <ul v-else class="divide-y divide-gray-100 dark:divide-gray-800">
        <li v-for="f in files" :key="f.id" class="flex items-start justify-between py-2 text-sm group">
          <span class="flex items-start gap-2 min-w-0">
            <UIcon name="i-heroicons-document" class="w-4 h-4 text-gray-400 shrink-0 mt-0.5" />
            <span class="min-w-0">
              <span class="flex items-center gap-2 min-w-0">
                <span class="truncate text-gray-700 dark:text-gray-300">{{ f.filename }}</span>
                <span :title="fateOf(f).title"
                      :class="['shrink-0 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full font-medium border', fateOf(f).class]">
                  <UIcon v-if="fateOf(f).icon" :name="fateOf(f).icon" class="w-3 h-3" />{{ fateOf(f).label }}
                </span>
                <!-- Confidence only for a machine's verdict. A conversion the
                     user asked for is not 100% "confident", it is simply theirs. -->
                <span v-if="confidenceLabel(f)" :title="decidedByTitle(f)"
                      class="shrink-0 text-[10px] text-gray-400 dark:text-gray-500 tabular-nums">{{ confidenceLabel(f) }}</span>
                <span v-else-if="isUserChoice(f)" title="You chose where this file goes."
                      class="shrink-0 text-[10px] text-gray-400 dark:text-gray-500">your choice</span>
              </span>
              <!-- Why it was filed this way. Produced on every upload and, until
                   now, written to a log and discarded — so a sound verdict and a
                   coin-flip looked identical. Absent on files ingested before the
                   record existed, which reads as "not known", never as doubt. -->
              <span v-if="intakeReason(f)"
                    class="block text-[11px] leading-snug text-gray-400 dark:text-gray-500 mt-0.5 pr-4">{{ intakeReason(f) }}</span>
            </span>
          </span>
          <span class="flex items-center gap-2 shrink-0 pt-0.5">
            <button v-if="canUpdate && fateOf(f).reingestable" @click="reingestFile(f)" :disabled="reingesting[f.id]"
                    title="Load this file's data as a queryable table the agent can query."
                    class="opacity-0 group-hover:opacity-100 text-[11px] text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50 disabled:no-underline">
              {{ reingesting[f.id] ? 'Re-ingesting…' : 'Re-ingest' }}
            </button>
            <!-- Convert. Every settled fate used to be a dead end: the badge was
                 final in the UI and the API took no destination. -->
            <UPopover v-if="canUpdate && canConvert(f)" :popper="{ placement: 'bottom-end' }">
              <button :disabled="converting[f.id]"
                      class="opacity-0 group-hover:opacity-100 focus:opacity-100 text-[11px] px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50">
                {{ converting[f.id] ? 'Converting…' : 'Convert' }}
              </button>
              <template #panel="{ close }">
                <div class="w-[290px] py-1">
                  <div class="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-800">
                    Move this file to
                  </div>
                  <button v-for="opt in convertOptions(f)" :key="opt.key"
                          @click="convertFile(f, opt.key); close()"
                          :class="['w-full text-left px-3 py-2 border-b border-gray-100 dark:border-gray-800 last:border-b-0 hover:bg-gray-50 dark:hover:bg-gray-800/60', opt.current ? 'bg-blue-50/60 dark:bg-blue-900/20' : '']">
                    <span class="flex items-center gap-2">
                      <span class="text-xs font-medium text-gray-800 dark:text-gray-200">{{ opt.label }}</span>
                      <span v-if="opt.current" class="ml-auto text-[9px] font-bold tracking-wide text-blue-600 dark:text-blue-400">NOW</span>
                    </span>
                    <!-- Framed by what the agent will DO with it. "Knowledge" and
                         "instruction" mean nothing to someone deciding; when the
                         agent sees the file does. -->
                    <span class="block text-[11px] leading-snug text-gray-500 dark:text-gray-400 mt-0.5">{{ opt.detail }}</span>
                  </button>
                  <label class="flex items-start gap-2 px-3 py-2 border-t border-gray-100 dark:border-gray-800 cursor-pointer">
                    <input type="checkbox" v-model="keepExisting" class="mt-0.5 shrink-0" />
                    <span class="text-[11px] leading-snug text-gray-500 dark:text-gray-400">
                      Keep the current filing as well — for a document that is genuinely both
                    </span>
                  </label>
                </div>
              </template>
            </UPopover>
            <button v-if="canUpdate" @click="removeFile(f)"
                    :title="f.source_kind === 'table_backing' ? 'Removes this file and the table built from it' : 'Remove file'"
                    class="opacity-0 group-hover:opacity-100 text-gray-300 hover:text-red-500"><UIcon name="i-heroicons-x-mark" class="w-4 h-4" /></button>
          </span>
        </li>
      </ul>
    </section>

    <!-- Directory connections -->
    <section v-for="conn in fileConnections" :key="conn.id" class="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <DataSourceIcon :type="conn.type" class="w-4 h-4" />
          <span class="text-sm font-semibold text-gray-900 dark:text-gray-100">{{ conn.name }}</span>
          <span class="text-[10px] uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ conn.type }}</span>
        </div>
        <div class="flex items-center gap-2">
          <span :class="badgeClass(indexModeOf(conn))" class="text-[10px] px-2 py-0.5 rounded-full font-medium">{{ badgeLabel(indexModeOf(conn)) }}</span>
          <button class="text-xs px-2 py-1 rounded-md border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800" @click="editScope(conn)">Edit scope</button>
        </div>
      </div>
      <div class="mt-3 grid grid-cols-[64px_1fr] gap-x-3 gap-y-1.5 text-xs">
        <span class="text-gray-400 dark:text-gray-500">Base</span>
        <span class="font-mono text-gray-700 dark:text-gray-300 break-all">{{ baseOf(conn) }}</span>
        <span class="text-gray-400 dark:text-gray-500">Scope</span>
        <div>
          <template v-if="globsOf(conn).length"><code v-for="g in globsOf(conn)" :key="g" class="inline-block mr-1.5 mb-1 px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-100 dark:border-blue-800">{{ g }}</code></template>
          <span v-else class="text-gray-500 italic">whole path</span>
        </div>
      </div>
      <div class="mt-3 border-t border-gray-100 dark:border-gray-800 pt-2">
        <div v-if="browse[conn.id]?.connectRequired" class="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 rounded px-2 py-1.5">
          Connect your account to browse — this connection reads files with each user's own credentials.
        </div>
        <template v-else>
        <!-- Browsing a delegated source is a live call to the provider and can
             take a while; say so instead of rendering "… files match", which
             reads like a value rather than a pending fetch. -->
        <div v-if="browse[conn.id] === undefined" class="text-xs text-gray-400 dark:text-gray-500 mb-1 flex items-center gap-1.5">
          <UIcon name="i-heroicons-arrow-path" class="w-3 h-3 animate-spin" />{{ $t('agentsPage.loadingFiles') }}
        </div>
        <div v-else class="text-xs text-gray-500 dark:text-gray-400 mb-1">{{ browse[conn.id]?.total ?? 0 }} files match · agent reads ONLY these · denials audited</div>
        <ul class="text-xs font-mono text-gray-600 dark:text-gray-400 space-y-0.5 max-h-48 overflow-auto">
          <li v-for="n in (browse[conn.id]?.names || [])" :key="n" class="truncate">{{ n }}</li>
          <li v-if="(browse[conn.id]?.total || 0) > (browse[conn.id]?.names?.length || 0)" class="text-gray-400 italic">… {{ browse[conn.id].total - browse[conn.id].names.length }} more</li>
          <li v-if="browse[conn.id] && browse[conn.id].total === 0" class="text-gray-400 italic">{{ indexModeOf(conn) === 'none' ? 'Live source — read on demand, not cached.' : 'No files match.' }}</li>
        </ul>
        </template>
      </div>
    </section>

    <div v-if="fileConnections.length === 0 && files.length === 0" class="text-sm text-gray-500 dark:text-gray-400 text-center py-4">No files or file connections yet.</div>
  </div>
</template>

<script setup lang="ts">
import DataSourceIcon from '~/components/DataSourceIcon.vue'
const props = defineProps<{ dsId: string; canUpdate?: boolean }>()
const emit = defineEmits(['edit-connection'])
const toast = useToast()

const connections = ref<any[]>([])
const registryByType = ref<Record<string, any>>({})
const files = ref<any[]>([])
const browse = ref<Record<string, { names: string[]; total: number; connectRequired?: boolean }>>({})
const uploading = ref(false)
const reingesting = ref<Record<string, boolean>>({})
const fileInput = ref<HTMLInputElement | null>(null)

// Per-file FATE badge. Prefer an explicit backend `fate` field; fall back to
// `source_kind` when the backend hasn't populated it. Degrades gracefully when
// both are absent → treated as a not-yet-ingested plain upload.
const fateOf = (f: any) => {
  const raw = String(f?.fate || f?.source_kind || 'upload').toLowerCase()
  switch (raw) {
    case 'table_backing':
    case 'table':
      return { label: 'In table', icon: 'i-heroicons-table-cells', reingestable: false,
        title: 'This file data is loaded as a queryable table. The agent queries the table instead of reading the raw file — no duplication. You can drop the file to keep only the table.',
        class: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border-blue-100 dark:border-blue-800' }
    case 'instruction':
    case 'instruction_backing':
      return { label: 'Instruction', icon: 'i-heroicons-book-open', reingestable: false,
        title: 'This file was turned into an agent instruction (definitions / glossary), always loaded as context.',
        class: 'bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300 border-purple-100 dark:border-purple-800' }
    case 'knowledge':
    case 'knowledge_backing':
      return { label: 'Knowledge', icon: 'i-heroicons-light-bulb', reingestable: false,
        title: 'This document is available as knowledge — the agent reads it on demand when relevant.',
        class: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-100 dark:border-emerald-800' }
    default: // 'upload' or unknown → not ingested
      return { label: 'Not ingested', icon: 'i-heroicons-exclamation-circle', reingestable: true,
        title: 'This file was uploaded but not yet loaded into a table, instruction, or knowledge. The agent cannot use it until re-ingested.',
        class: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400 border-gray-200 dark:border-gray-700' }
  }
}

const fileConnections = computed(() => connections.value.filter((c) => registryByType.value[c.type]?.data_shape === 'files'))
const cfg = (c: any) => c?.config || {}
const baseOf = (c: any) => cfg(c).bucket ? `s3://${cfg(c).bucket}/${cfg(c).prefix || ''}` : (cfg(c).root_path || '—')
const globsOf = (c: any) => String(cfg(c).include_globs || '').split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
const indexModeOf = (c: any) => cfg(c).index_mode || (cfg(c).index_content === false ? 'metadata' : 'content')
const badgeLabel = (m: string) => ({ none: 'Live', metadata: 'Indexed: list', content: 'Indexed: contents' } as any)[m] || m
const badgeClass = (m: string) => ({ none: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300', metadata: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300', content: 'bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300' } as any)[m] || 'bg-gray-100 text-gray-600'

async function loadAll() {
  if (!props.dsId) return
  const [reg, conns, ups] = await Promise.all([
    useMyFetch('/available_data_sources', { method: 'GET' }),
    useMyFetch(`/data_sources/${props.dsId}/connections`, { method: 'GET' }),
    useMyFetch(`/data_sources/${props.dsId}/files`, { method: 'GET' }),
  ])
  for (const e of (reg.data.value as any[]) || []) registryByType.value[e.type] = e
  connections.value = (conns.data.value as any[]) || []
  files.value = (ups.data.value as any[]) || []
  for (const c of fileConnections.value) {
    try {
      // Live list — same path the agent's list_files uses (source of truth),
      // so browse never diverges and none-mode connections show their files.
      const res = await useMyFetch(`/data_sources/${props.dsId}/connections/${c.id}/files?limit=30`, { method: 'GET' })
      const d: any = res.data.value || {}
      // Show the human-readable name. Connectors whose ids ARE paths
      // (network_dir, S3) read fine either way, but Graph sources return opaque
      // item ids — preferring `id` listed a OneDrive/SharePoint library as
      // "01TP3T7WAPS6ZPWYNEKFDLOFCUKPKFKA54" instead of "Book 1.xlsx".
      browse.value[c.id] = { names: (d.files || []).map((f: any) => f.name || f.path || f.id), total: d.total ?? (d.files || []).length, connectRequired: !!d.connect_required }
    } catch { browse.value[c.id] = { names: [], total: 0 } }
  }
}
function triggerUpload() { fileInput.value?.click() }
async function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return
  uploading.value = true
  try {
    for (const file of Array.from(input.files)) {
      const fd = new FormData(); fd.append('file', file)
      const { data, error } = await useMyFetch(`/data_sources/${props.dsId}/files`, { method: 'POST', body: fd })
      if (error.value || !data.value) { toast.add({ title: 'Upload failed', description: file.name, color: 'red' }); continue }
      files.value.push(data.value as any)
    }
  } finally { uploading.value = false; if (input) input.value = '' }
}
// ── how a file was filed, and how to re-file it ─────────────────────────────
// The backend records the librarian's verdict on File.preview["intake"] and
// serves it as `intake`. Every accessor below tolerates its absence: files
// ingested before the record existed have none, and that must read as "not
// known" rather than as a low-confidence guess on a call that may well be right.
const intakeOf = (f: any) => (f && typeof f.intake === 'object' && f.intake) || null
const intakeReason = (f: any) => intakeOf(f)?.reason || ''
const isUserChoice = (f: any) => intakeOf(f)?.decided_by === 'user'

const confidenceLabel = (f: any) => {
  const i = intakeOf(f)
  if (!i || i.decided_by === 'user') return ''
  const c = Number(i.confidence)
  return Number.isFinite(c) ? `${Math.round(c * 100)}%` : ''
}

const decidedByTitle = (f: any) => {
  const by = intakeOf(f)?.decided_by
  if (by === 'llm') return 'An AI read this file and chose where it belongs.'
  if (by === 'deterministic') return 'Chosen from the file\'s shape and extension — its contents were not read.'
  return ''
}

// Destinations, described by WHEN the agent sees them rather than by name.
const CONVERT_TARGETS = [
  { key: 'instruction', label: 'Instruction — definitions and rules',
    detail: 'Loaded into context whenever it might be relevant.' },
  { key: 'skill', label: 'Skill — a procedure to follow',
    detail: 'Pulled by name when a question matches it.' },
  { key: 'knowledge', label: 'Knowledge — reference material',
    detail: 'Searched only when the agent decides to look.' },
  { key: 'table', label: 'Table — queryable rows',
    detail: 'For spreadsheet data. Not available for documents.' },
]

// A table-backing file is excluded: its data is already materialized, and
// re-routing it would double-handle the same rows. The backend refuses this too
// (reingest_file returns skipped) — the button is hidden so the refusal is never
// something the user has to discover by clicking.
const canConvert = (f: any) => (f?.fate || f?.source_kind) !== 'table_backing'

const convertOptions = (f: any) => {
  const current = f?.fate === 'table_backing' ? 'table' : f?.fate
  const isTabular = /\.(csv|xlsx|xls|tsv)$/i.test(f?.filename || '')
  return CONVERT_TARGETS
    .filter((t) => t.key !== 'table' || isTabular)
    .map((t) => ({ ...t, current: t.key === current }))
}

const converting = ref<Record<string, boolean>>({})
const keepExisting = ref(false)

// Converting REPLACES what the file produced before, unless "keep" is ticked.
// That is the right default — a conversion is usually a correction — but it
// destroys work that is not visible from this row: the passages the agent reads,
// or the rules written from the document. Same fault as the delete: a cheap
// click with a consequence living somewhere else on the screen.
function conversionConsequence(f: any, destination: string): string | null {
  if (keepExisting.value) return null  // nothing is replaced; nothing to warn about
  const fate = f?.fate || f?.source_kind
  const head = `Convert "${f.filename}" to ${destination}?`
  if (fate === 'knowledge' || fate === 'knowledge_backing') {
    return `${head}\n\nThe passages the agent currently reads from this document are replaced. Tick "keep the current filing" first if you want both.`
  }
  if (fate === 'instruction' || fate === 'instruction_backing') {
    return `${head}\n\nThe rules currently written from this document are replaced. Tick "keep the current filing" first if you want both.`
  }
  return null  // nothing was produced from it yet, so nothing is being replaced
}

async function convertFile(f: any, destination: string) {
  if (converting.value[f.id]) return
  const warning = conversionConsequence(f, destination)
  if (warning && !window.confirm(warning)) return
  converting.value = { ...converting.value, [f.id]: true }
  try {
    const q = `destination=${destination}${keepExisting.value ? '&keep_existing=true' : ''}`
    const { data, error } = await useMyFetch(
      `/data_sources/${props.dsId}/files/${f.id}/reingest?${q}`, { method: 'POST' },
    )
    if (error.value) throw error.value
    const created = (data.value as any)?.created
    toast.add({
      title: `Converted to ${destination}`,
      // Says what it produced. A conversion that reports only success leaves the
      // user unable to tell a rewrite from a no-op.
      description: created ? `${f.filename} — ${created} item(s) written` : f.filename,
      color: 'green',
    })
    await loadAll()
  } catch (e: any) {
    toast.add({
      title: 'Convert failed',
      description: e?.data?.detail || f.filename,
      color: 'red',
    })
  } finally {
    const { [f.id]: _drop, ...rest } = converting.value
    converting.value = rest
  }
}

async function reingestFile(f: any) {
  if (reingesting.value[f.id]) return
  reingesting.value = { ...reingesting.value, [f.id]: true }
  try {
    const { error } = await useMyFetch(`/data_sources/${props.dsId}/files/${f.id}/reingest`, { method: 'POST' })
    if (error.value) throw error.value
    toast.add({ title: 'File re-ingested', description: f.filename, color: 'green' })
    await loadAll()
  } catch {
    toast.add({ title: 'Re-ingest failed', description: f.filename, color: 'red' })
  } finally {
    const { [f.id]: _drop, ...rest } = reingesting.value
    reingesting.value = rest
  }
}
// What removing this file will actually cost, in the user's terms.
// This delete used to detach a file and leave everything it had produced in
// place, so acting on a single click was survivable. It now withdraws the table
// built from the file as well — a much larger consequence, and one that is
// invisible from this panel because the tables live on another tab. Six files
// were destroyed by six unconfirmed clicks before this existed.
function removalConsequence(f: any): string {
  const fate = f?.fate || f?.source_kind
  if (fate === 'table_backing') {
    return `Remove "${f.filename}"?\n\nThe table built from this file is removed too, and the agent will no longer be able to query its data.`
  }
  if (fate === 'knowledge' || fate === 'knowledge_backing') {
    return `Remove "${f.filename}"?\n\nThe passages the agent reads from this document are removed with it.`
  }
  if (fate === 'instruction' || fate === 'instruction_backing') {
    return `Remove "${f.filename}"?\n\nThe rules written from this document stay, but the document behind them is gone.`
  }
  return `Remove "${f.filename}"?`
}

async function removeFile(f: any) {
  // Deliberately asked every time, with no "don't ask again". The cost is not
  // recoverable from this screen, and the thing being destroyed is not on it.
  if (!window.confirm(removalConsequence(f))) return
  try {
    const { data } = await useMyFetch(`/data_sources/${props.dsId}/files/${f.id}`, { method: 'DELETE' })
    files.value = files.value.filter((x) => x.id !== f.id)
    // Removing a file now also withdraws the table built from it. That is a
    // bigger consequence than "file removed" implies, so it is reported rather
    // than left for the user to notice next time they open the Tables tab.
    const withdrawn = ((data.value as any)?.removed_paths || []).length
    if (withdrawn) {
      toast.add({
        title: 'File and its table removed',
        description: `${f.filename} — the table built from it is no longer available`,
        color: 'green',
      })
    }
  }
  catch { toast.add({ title: 'Failed to remove file', color: 'red' }) }
}
// Scope lives on the connection — let the host open the ConnectionDetailModal.
function editScope(conn: any) { emit('edit-connection', conn) }
watch(() => props.dsId, loadAll, { immediate: true })
</script>
