<template>
  <UModal v-model="isOpen" :ui="{ width: 'sm:max-w-2xl' }" :prevent-close="step !== 'connect'">
    <div class="p-5">
      <!-- Header -->
      <div class="flex items-center justify-between mb-1">
        <h3 class="text-lg font-semibold text-gray-900 dark:text-white">{{ isUploadOnly ? 'New Data Agent' : 'Create Data Agent' }}</h3>
        <button class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300" @click="isOpen = false">
          <UIcon name="heroicons-x-mark" class="w-5 h-5" />
        </button>
      </div>
      <p class="text-sm text-gray-500 dark:text-gray-400">{{ isUploadOnly ? 'Upload files — the agent sorts them into tables, rules & knowledge.' : 'Set data source, select tables, and define additional context' }}</p>

      <!-- Stepper -->
      <nav class="w-full my-5">
        <ol class="flex justify-center items-center gap-4 text-xs">
          <li v-for="(s, idx) in steps" :key="s.key" class="flex items-center gap-2">
            <span class="flex items-center gap-2">
              <span :class="circleClass(s.key)" class="w-5 h-5 rounded-full flex items-center justify-center">
                <UIcon v-if="isDone(s.key)" name="heroicons-check" class="w-3.5 h-3.5" />
                <span v-else>{{ idx + 1 }}</span>
              </span>
              <span :class="s.key === step ? 'text-gray-900 dark:text-white' : 'text-gray-500 dark:text-gray-400'">{{ s.label }}</span>
            </span>
            <span v-if="idx < steps.length - 1" class="mx-2 w-6 h-px bg-gray-200 dark:bg-gray-800"></span>
          </li>
        </ol>
      </nav>

      <!-- ── Step 1: Connection ───────────────────────────────────── -->
      <div v-if="step === 'connect'">
        <!-- Loading connections -->
        <div v-if="loadingConnections" class="flex flex-col items-center justify-center py-16">
          <Spinner class="h-4 w-4 text-gray-400 dark:text-gray-500" />
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-2">Loading connections...</p>
        </div>

        <div v-else class="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800 p-4">
          <!-- Agent name -->
          <div class="mb-4">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Name <span class="text-red-500">*</span>
            </label>
            <UInput
              v-model="agentName"
              placeholder="e.g., Sales, Marketing, Finance"
              size="lg"
              :disabled="creatingFromConnection"
            />
          </div>

          <!-- Connector agent connects an existing data source only. File
               uploads live in the dedicated "Data Agent" entry (upload-only),
               so the old "Upload files" card is intentionally NOT shown here. -->

          <!-- Upload files flow -->
          <div v-if="sourceMode === 'upload'">
            <div class="mb-4">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Files <span class="text-red-500">*</span>
              </label>
              <!-- Drag-and-drop zone (click to browse). The hidden native input
                   still drives selection so behaviour matches the old control. -->
              <div
                class="relative rounded-xl border-2 border-dashed px-4 py-6 text-center transition-colors cursor-pointer"
                :class="dragOver
                  ? 'border-blue-500 bg-blue-50/70 dark:bg-blue-950/40'
                  : 'border-blue-200 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-950/20 hover:border-blue-300'"
                @click="uploadInput?.click()"
                @dragover.prevent="dragOver = true"
                @dragleave.prevent="dragOver = false"
                @drop.prevent="onDropFiles"
              >
                <input
                  ref="uploadInput"
                  type="file"
                  accept=".csv,.xlsx,.docx,.pdf,.pptx,text/csv"
                  multiple
                  class="hidden"
                  :disabled="uploadingFiles"
                  @change="onUploadFilesSelected"
                />
                <div class="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-lg bg-white dark:bg-gray-900 border border-blue-200 dark:border-blue-900">
                  <UIcon name="heroicons-arrow-up-tray" class="h-5 w-5 text-blue-500" />
                </div>
                <div class="text-sm font-medium text-gray-800 dark:text-gray-100">Drag &amp; drop your files</div>
                <div class="text-xs text-gray-500 dark:text-gray-400">or <span class="text-blue-600 dark:text-blue-400 font-medium">browse</span> — the agent detects what each file is</div>
                <div class="mt-3 flex flex-wrap justify-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400">
                  <span class="rounded-full border border-gray-200 dark:border-gray-700 px-2 py-0.5">CSV / Excel → Tables</span>
                  <span class="rounded-full border border-gray-200 dark:border-gray-700 px-2 py-0.5">Definitions → Instructions</span>
                  <span class="rounded-full border border-gray-200 dark:border-gray-700 px-2 py-0.5">Word / PDF → Knowledge</span>
                </div>
              </div>
              <ul v-if="uploadFiles.length" class="mt-2 space-y-1">
                <li
                  v-for="(f, i) in uploadFiles"
                  :key="i"
                  class="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-800 px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300"
                >
                  <UIcon name="heroicons-document-text" class="h-3.5 w-3.5 flex-shrink-0 text-gray-400" />
                  <span class="truncate flex-1">{{ f.name }}</span>
                  <button type="button" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200" @click.stop="removeUploadFile(i)">
                    <UIcon name="heroicons-x-mark" class="h-3.5 w-3.5" />
                  </button>
                </li>
              </ul>
              <p class="mt-2 text-[11px] text-gray-400">Auto-sorted on upload; you review &amp; toggle detected tables on the next step.</p>
            </div>

            <!-- Same "learn" option as the connector flow — generates an overview
                 instruction from the uploaded data. -->
            <div class="mb-4 flex items-center gap-2">
              <UToggle v-model="useLlmSync" :disabled="uploadingFiles" size="xs" color="blue" />
              <span class="text-xs text-gray-700 dark:text-gray-300">Use LLM to learn agent</span>
            </div>

            <div v-if="uploadError" class="p-3 bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400 rounded-lg text-sm mb-4">
              {{ uploadError }}
            </div>

            <div class="flex justify-between items-center pt-4 border-t border-gray-100 dark:border-gray-800">
              <button class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300" @click="isOpen = false">
                ← Cancel
              </button>
              <UButton
                color="blue"
                size="xs"
                :loading="uploadingFiles"
                :disabled="!agentName.trim() || !uploadFiles.length || uploadingFiles"
                @click="createFromUpload"
              >
                {{ uploadingFiles ? 'Uploading…' : 'Create & Continue' }}
              </UButton>
            </div>
          </div>

          <!-- Connection selector (multi-select for existing connections) -->
          <div v-if="sourceMode === 'connect'">
          <div class="mb-4">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Connections <span class="text-red-500">*</span>
            </label>
            <USelectMenu
              v-model="selectedConnections"
              :options="connections"
              placeholder="Select connections"
              size="lg"
              :disabled="creatingFromConnection"
              by="id"
              multiple
              searchable
              searchable-placeholder="Search connections..."
              option-attribute="name"
              :search-attributes="['name', 'type']"
            >
              <template #label>
                <div v-if="selectedConnections.length > 0" class="flex items-center gap-1.5 flex-wrap">
                  <template v-for="conn in selectedConnections" :key="conn.id">
                    <div class="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5">
                      <DataSourceIcon :type="conn.type" :connector-key="conn.connector_key" class="h-3.5 flex-shrink-0" />
                      <span class="text-xs truncate max-w-[100px]">{{ conn.name }}</span>
                    </div>
                  </template>
                </div>
                <span v-else class="text-gray-400 dark:text-gray-500">Select connections</span>
              </template>
              <template #option="{ option }">
                <div class="flex items-center gap-2 w-full">
                  <DataSourceIcon :type="option.type" :connector-key="option.connector_key" class="h-4 flex-shrink-0" />
                  <div class="flex-1 min-w-0">
                    <div class="font-medium truncate">{{ option.name }}</div>
                    <div class="text-[10px] text-gray-400 dark:text-gray-500">
                      {{ connectionCountLabel(option) }} · {{ option.agent_count || 0 }} agents
                    </div>
                  </div>
                </div>
              </template>
            </USelectMenu>
            <button
              type="button"
              class="mt-2 inline-flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700"
              :disabled="creatingFromConnection"
              @click="showAddConnectionModal = true"
            >
              <UIcon name="heroicons-plus-circle" class="h-3.5 w-3.5" />
              <span>Create new connection</span>
            </button>
          </div>

          <!-- Existing connection flow (main form) -->
          <div v-if="selectedConnections.length > 0">
            <div class="flex items-center gap-2 mb-4">
              <UToggle v-model="useLlmSync" :disabled="creatingFromConnection" size="xs" color="blue" />
              <span class="text-xs text-gray-700 dark:text-gray-300">Use LLM to learn agent</span>
            </div>

            <div v-if="dupHint" class="flex items-start gap-2 p-3 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300 rounded-lg text-sm mb-4">
              <UIcon name="heroicons-information-circle" class="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>{{ dupHint }}</span>
            </div>

            <div v-if="errorMessage" class="p-3 bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400 rounded-lg text-sm mb-4">
              {{ errorMessage }}
            </div>

            <div class="flex justify-between items-center pt-4 border-t border-gray-100 dark:border-gray-800">
              <button class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300" @click="isOpen = false">
                ← Cancel
              </button>
              <UButton
                color="blue"
                size="xs"
                :loading="creatingFromConnection"
                :disabled="!canSubmitExisting"
                @click="createAgentFromExistingConnection"
              >
                Save & Continue
              </UButton>
            </div>
          </div>

          <!-- No selection yet (just show cancel) -->
          <div v-else class="flex justify-start pt-4 border-t border-gray-100 dark:border-gray-800">
            <button class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300" @click="isOpen = false">
              ← Cancel
            </button>
          </div>
          </div>
        </div>
      </div>

      <!-- ── Step 2: Configure knowledge (tables / files / tools) ──── -->
      <div v-else-if="step === 'schema'">
        <p class="text-sm text-gray-500 dark:text-gray-400 text-center mb-4">{{ isUploadOnly ? 'Review the tables detected from your files — toggle off anything you don’t need. Definitions and documents were sorted automatically.' : 'Pick tables for databases, review the file scope for directories — each source its own way.' }}</p>
        <!-- Background LLM learn kicked off by the last upload — non-blocking. -->
        <div v-if="backgroundLearning" class="flex items-center justify-center gap-1.5 mb-3 text-[11px] text-gray-400 dark:text-gray-500">
          <Spinner class="w-3 h-3" />
          <span>Learning agent in background…</span>
        </div>
        <div class="bg-white dark:bg-gray-900 rounded-lg">
          <AgentKnowledgeTabs :ds-id="dsId" continue-label="Save & Continue" @saved="step = 'context'" />
        </div>
      </div>

      <!-- ── Step 3: Set Context ──────────────────────────────────── -->
      <div v-else-if="step === 'context'">
        <div class="space-y-6">
          <!-- Instruction editor -->
          <div>
            <h3 class="text-base font-semibold text-gray-900 dark:text-white mb-1">Add custom AI rules and instructions</h3>
            <p class="text-sm text-gray-500 dark:text-gray-400 mb-3">Business-specific context, glossary, and useful code guidelines.</p>

            <div class="border border-gray-200 dark:border-gray-800 rounded-md px-3 py-2 focus-within:ring-2 focus-within:ring-blue-100 focus-within:border-blue-400">
              <!-- Loading overlay -->
              <div v-if="loadingDraft" class="flex items-center justify-center gap-2 py-10 text-xs text-gray-400 dark:text-gray-500">
                <Spinner class="w-4 h-4" />
                <span>Generating overview instruction…</span>
              </div>

              <InstructionEditor
                v-else
                v-model="instructionText"
                mode="wysiwyg"
                :editable="true"
                :data-source-ids="dsId ? [dsId] : []"
                placeholder="Describe business rules, metric definitions, or query guidelines… (type @ to mention a table or instruction)"
              />
            </div>
          </div>

          <!-- Git integration — only shown when no repo is connected -->
          <div v-if="!integration?.git_repository" class="flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500">
            <GitBranchIcon class="w-3.5 h-3.5" />
            <span>Connect a git repository for Tableau, dbt, and markdown context —</span>
            <button class="text-blue-500 hover:text-blue-600 underline-offset-2 hover:underline" @click="showGitModal = true">integrate</button>
          </div>

          <div class="flex justify-end pt-4">
            <button @click="handleSave" :disabled="saving || loadingDraft" class="bg-blue-500 hover:bg-blue-600 text-white text-xs font-medium py-1.5 px-3 rounded disabled:opacity-50">
              <span v-if="saving">Saving...</span>
              <span v-else>Finish</span>
            </button>
          </div>

          <GitRepoModalComponent v-model="showGitModal" :datasource-id="String(dsId)" :git-repository="integration?.git_repository" :metadata-resources="{ resources: [] }" @update:modelValue="handleGitModalClose" />
        </div>
      </div>
    </div>

    <!-- Add Connection Modal (nested — for creating a brand new connection) -->
    <AddConnectionModal v-model="showAddConnectionModal" :skipSuccessStep="true" @created="handleNewConnectionCreated" />
  </UModal>
</template>

<script setup lang="ts">
import Spinner from '~/components/Spinner.vue'
import AgentKnowledgeTabs from '@/components/datasources/AgentKnowledgeTabs.vue'
import AddConnectionModal from '~/components/AddConnectionModal.vue'
import GitRepoModalComponent from '@/components/GitRepoModalComponent.vue'
import DataSourceIcon from '~/components/DataSourceIcon.vue'
import GitBranchIcon from '~/components/icons/GitBranchIcon.vue'
import InstructionEditor from '~/components/instructions/InstructionEditor.vue'
import { connectionCatalogLabel } from '~/composables/useCatalogCount'

const props = defineProps<{
  modelValue: boolean
  // Which source tab the modal opens on. 'connect' (default) = today's behavior;
  // 'upload' opens straight on the Upload-files flow for a pure Data Agent.
  initialMode?: 'connect' | 'upload'
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'finished', dsId: string): void
}>()

const isOpen = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// ── Wizard step state ───────────────────────────────────────────────────────
// Dedicated "Data Agent" entry: the modal is opened straight in upload mode and
// the connector chrome (source-picker cards, DB-flavoured step labels) is dropped.
const isUploadOnly = computed(() => props.initialMode === 'upload')
// Upload-native step labels for a Data Agent; connector labels otherwise.
const steps = computed(() => isUploadOnly.value
  ? [
      { key: 'connect', label: 'Upload' },
      { key: 'schema', label: 'Review' },
      { key: 'context', label: 'Set context' },
    ]
  : [
      { key: 'connect', label: 'Connection' },
      { key: 'schema', label: 'Select Tables' },
      { key: 'context', label: 'Set Context' },
    ])
const step = ref<'connect' | 'schema' | 'context'>('connect')
const order = ['connect', 'schema', 'context']
function isDone(key: string) {
  return order.indexOf(key) < order.indexOf(step.value)
}
function circleClass(key: string) {
  if (isDone(key)) return 'bg-green-100 text-green-600 dark:bg-green-500/10 dark:text-green-400'
  if (key === step.value) return 'bg-gray-900 text-white'
  return 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
}

// The created agent / data source id (carries across steps)
const dsId = ref('')

// Reset everything when the modal opens.
watch(isOpen, (val) => {
  if (val) {
    step.value = 'connect'
    dsId.value = ''
    agentName.value = ''
    selectedConnections.value = []
    useLlmSync.value = true
    creatingFromConnection.value = false
    errorMessage.value = ''
    sourceMode.value = props.initialMode || 'connect'
    uploadFiles.value = []
    uploadingFiles.value = false
    uploadError.value = ''
    backgroundLearning.value = false
    instructionText.value = ''
    draftInstructionId.value = null
    integration.value = null
    loadConnections()
  }
})

// ── Step 1: Connection ──────────────────────────────────────────────────────
interface Connection {
  id: string
  name: string
  type: string
  connector_key?: string | null
  // Registry data_shape (tables | files | objects | tools) — drives which noun
  // the catalog count uses. Sent by GET /connections.
  data_shape?: string
  table_count?: number
  tool_count?: number
  agent_count?: number
}

// "11 files" / "3 tools" / "12 tables" — the noun follows the connection's
// data_shape rather than a hardcoded type list.
const connectionCountLabel = connectionCatalogLabel

const connections = ref<Connection[]>([])
const loadingConnections = ref(true)
const selectedConnections = ref<Connection[]>([])
// Neutral heads-up (NOT a block): a connection can back multiple agents, so we
// just note when a selected one already has an agent. Never gates creation.
const dupHint = computed(() => {
  const dup = selectedConnections.value.filter((c: any) => (c.agent_count || 0) > 0)
  if (!dup.length) return ''
  const names = dup.map((c: any) => c.name).join(', ')
  return `${names} already ${dup.length > 1 ? 'have agents' : 'has an agent'}. One connection can back multiple agents — this adds another on the same data.`
})
const agentName = ref('')
const useLlmSync = ref(true)
const creatingFromConnection = ref(false)
const errorMessage = ref('')
const showAddConnectionModal = ref(false)

// Source mode: connect an existing data source vs upload your own files.
const sourceMode = ref<'connect' | 'upload'>('connect')
const uploadFiles = ref<File[]>([])
const uploadingFiles = ref(false)
const uploadError = ref('')
// Non-blocking "learning agent in background…" indicator shown on the Review
// step after an upload when LLM learning is on. Cleared once the Set-context
// step resolves the onboarding draft.
const backgroundLearning = ref(false)

function onUploadFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  uploadFiles.value = input.files ? Array.from(input.files) : []
  uploadError.value = ''
}

// Drag-and-drop support for the upload dropzone.
const uploadInput = ref<HTMLInputElement | null>(null)
const dragOver = ref(false)
function onDropFiles(e: DragEvent) {
  dragOver.value = false
  const dropped = e.dataTransfer?.files
  if (dropped && dropped.length) {
    uploadFiles.value = Array.from(dropped)
    uploadError.value = ''
  }
}
function removeUploadFile(i: number) {
  uploadFiles.value = uploadFiles.value.filter((_, idx) => idx !== i)
}

// Build a private file-agent from uploaded files, then continue into the wizard.
async function createFromUpload() {
  if (!agentName.value?.trim() || !uploadFiles.value.length) return
  uploadingFiles.value = true
  uploadError.value = ''
  try {
    const res = await useMyFetch('/data_sources', {
      method: 'POST',
      body: { name: agentName.value.trim(), type: 'csv', config: { file_paths: '' }, is_public: false, use_llm_sync: useLlmSync.value },
    })
    if (res.error?.value || !res.data?.value?.id) { uploadError.value = 'Failed to create agent'; return }
    const id = res.data.value.id
    // Upload sequentially. The backend now reflects each file's schema
    // synchronously inside the POST (no separate refresh_schema needed — the
    // Review step still has its Reload button as a fallback). Only the LAST file
    // triggers the (background) LLM learn: every earlier file passes
    // `learn=false` so we don't fire N redundant learns while uploading.
    const files = uploadFiles.value
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      const isLast = i === files.length - 1
      const fd = new FormData()
      fd.append('file', f)
      const up = await useMyFetch(`/data_sources/${id}/files${isLast ? '' : '?learn=false'}`, { method: 'POST', body: fd })
      if (up.error?.value) { uploadError.value = `Failed to upload ${f.name}`; return }
    }
    dsId.value = id
    // If learning is on, the last upload kicked off a background LLM learn —
    // surface a non-blocking chip on the Review step until the draft is ready
    // (cleared once the Set-context step finishes loading the draft).
    backgroundLearning.value = useLlmSync.value
    step.value = 'schema'
  } catch (e: any) {
    uploadError.value = e?.data?.detail || e?.message || 'Upload failed'
  } finally {
    uploadingFiles.value = false
  }
}

const canSubmitExisting = computed(() =>
  selectedConnections.value.length > 0 &&
  agentName.value.trim().length > 0 &&
  !creatingFromConnection.value
)

async function loadConnections() {
  loadingConnections.value = true
  try {
    const response = await useMyFetch('/connections', { method: 'GET' })
    connections.value = (response.data.value || []) as Connection[]
    // Single connection — auto-select it (matches /agents/new behaviour).
    if (connections.value.length === 1 && selectedConnections.value.length === 0) {
      selectedConnections.value = [connections.value[0]]
    }
  } catch (err) {
    console.error('Failed to load connections:', err)
  } finally {
    loadingConnections.value = false
  }
}

async function handleNewConnectionCreated(connectionData: any) {
  await loadConnections()
  if (connectionData?.id) {
    const newConn = connections.value.find(c => c.id === connectionData.id)
    if (newConn && !selectedConnections.value.some(c => c.id === newConn.id)) {
      selectedConnections.value = [...selectedConnections.value, newConn]
    }
  }
}

async function createAgentFromExistingConnection() {
  if (selectedConnections.value.length === 0 || !agentName.value.trim()) return
  // One connection can back multiple agents (same as bow). We surface a neutral
  // heads-up note (`dupHint`) when the chosen connection already has an agent,
  // but never block — first click creates.
  creatingFromConnection.value = true
  errorMessage.value = ''
  try {
    const payload: Record<string, any> = {
      name: agentName.value.trim(),
      connection_ids: selectedConnections.value.map(c => c.id),
      use_llm_sync: useLlmSync.value,
      is_public: false,
      generate_summary: false,
      generate_conversation_starters: false,
      generate_ai_rules: false,
    }
    const response = await useMyFetch('/data_sources', { method: 'POST', body: payload })
    if (response.error.value) {
      const errData = (response.error.value as any).data as any
      errorMessage.value = errData?.detail || 'Failed to create agent'
      return
    }
    const result = response.data.value as any
    if (result?.id) {
      dsId.value = result.id
      step.value = 'schema'
    } else {
      isOpen.value = false
    }
  } catch (err: any) {
    errorMessage.value = err?.message || 'An error occurred'
  } finally {
    creatingFromConnection.value = false
  }
}

// ── Step 3: Set Context ─────────────────────────────────────────────────────
const saving = ref(false)
const loadingDraft = ref(false)
const showGitModal = ref(false)
const integration = ref<any>(null)
const draftInstructionId = ref<string | null>(null)
const instructionText = ref('')

// Kick off the context step's data loads when we arrive on it.
watch(step, (s) => {
  if (s === 'context') {
    fetchIntegration()
    loadDraftInstruction()
  }
})

async function fetchIntegration() {
  if (!dsId.value) return
  const response = await useMyFetch(`/data_sources/${dsId.value}`, { method: 'GET' })
  if ((response.status as any)?.value === 'success') integration.value = (response.data as any)?.value
}

function handleGitModalClose(value: boolean) { if (!value) fetchIntegration() }

async function loadDraftInstruction() {
  if (!dsId.value) return
  // Honor the "Use LLM to learn agent" toggle — skip overview generation when
  // the user turned learning off (default is on, so normal flow is unchanged).
  if (!useLlmSync.value) return
  loadingDraft.value = true
  try {
    // The last upload already kicked off a background LLM learn. POLL the
    // onboarding-draft endpoint until the overview is ready, instead of POSTing
    // llm_sync ourselves — that used to REGENERATE the whole overview a second
    // time (double LLM spend). ~40s budget (16 × 2.5s).
    const maxAttempts = 16
    const intervalMs = 2500
    let loaded = false
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      // Bail if the user navigated away from the context step (modal closed / back).
      if (step.value !== 'context') break
      try {
        const { data, error } = await useMyFetch<any>(`/data_sources/${dsId.value}/onboarding_instruction`, { method: 'GET' })
        // 200 with a non-empty draft = ready. A 404 means "not ready yet" (or the
        // endpoint is absent) — either way keep polling, then fall back below.
        if (!error.value && data.value?.id && (data.value.text || '').trim()) {
          instructionText.value = data.value.text || ''
          draftInstructionId.value = data.value.id
          loaded = true
          break
        }
      } catch { /* transient / missing endpoint — keep polling */ }
      await new Promise((r) => setTimeout(r, intervalMs))
    }

    if (!loaded) {
      // Fallback (polling exhausted or endpoint missing): a SINGLE llm_sync, same
      // as the original flow. The backend force-syncs the schema inside llm_sync.
      const { data: syncData } = await useMyFetch<any>(`/data_sources/${dsId.value}/llm_sync`, { method: 'POST' })
      const instructionId: string | undefined = syncData.value?.onboarding_instruction?.id
      if (instructionId) {
        const { data, error } = await useMyFetch<any>(`/instructions/${instructionId}`, { method: 'GET' })
        if (!error.value && data.value) {
          instructionText.value = data.value.text || ''
          draftInstructionId.value = instructionId
        }
      }
    }
  } catch {} finally {
    loadingDraft.value = false
    backgroundLearning.value = false
  }
}

// ── Save (final step) ───────────────────────────────────────────────────────
async function handleSave() {
  if (saving.value) return
  saving.value = true
  try {
    const text = instructionText.value.trim()
    let primaryInstructionId: string | null = null

    if (draftInstructionId.value) {
      if (text) {
        await useMyFetch(`/instructions/${draftInstructionId.value}`, {
          method: 'PUT',
          body: { text, status: 'published' },
        })
        primaryInstructionId = draftInstructionId.value
      } else {
        await useMyFetch(`/instructions/${draftInstructionId.value}`, { method: 'DELETE' })
      }
    } else if (text) {
      const { data } = await useMyFetch('/instructions/global', {
        method: 'POST',
        body: {
          text,
          status: 'published',
          category: 'general',
          is_seen: true,
          can_user_toggle: true,
          load_mode: 'always',
          data_source_ids: [dsId.value],
        },
      })
      primaryInstructionId = (data as any)?.value?.id || null
    }

    if (primaryInstructionId) {
      await useMyFetch(`/data_sources/${dsId.value}`, {
        method: 'PUT',
        body: { primary_instruction_id: primaryInstructionId },
      })
    }

    const created = dsId.value
    isOpen.value = false
    emit('finished', created)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.instruction-wysiwyg {
  min-height: 280px;
}
</style>
