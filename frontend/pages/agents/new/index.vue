<template>
  <div class="flex ps-2 md:ps-4 text-sm mx-auto md:w-1/2 md:pt-10">
    <div class="w-full px-4 ps-0 py-4">
      <div>
        <h1 class="text-lg font-semibold text-center">Create Data Agent</h1>
        <p class="mt-2 text-gray-500 dark:text-gray-400 text-center">Set data source, select tables, and define additional context</p>
      </div>

      <WizardSteps class="mt-7" current="connect" />

      <!-- Loading connections -->
      <div v-if="loadingConnections" class="flex flex-col items-center justify-center py-16">
        <Spinner class="h-4 w-4 text-gray-400" />
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-2">Loading connections...</p>
      </div>

      <div v-else class="mt-6 bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <!-- Agent name -->
        <div class="mb-4">
          <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Name <span class="text-red-500">*</span>
          </label>
          <UInput
            v-model="agentName"
            placeholder="e.g., Sales, Marketing, Finance"
            size="lg"
            :disabled="creatingFromConnection || uploadingCsv"
          />
        </div>

        <!-- Source mode selector: existing connection vs upload CSV files -->
        <div class="mb-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            :disabled="creatingFromConnection || uploadingCsv"
            class="flex items-center gap-2 rounded-lg border p-3 text-left transition-colors"
            :class="mode === 'connection'
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-950'
              : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'"
            @click="mode = 'connection'"
          >
            <UIcon name="heroicons-circle-stack" class="h-5 w-5 text-gray-500 flex-shrink-0" />
            <div class="min-w-0">
              <div class="text-sm font-medium">Connect a data source</div>
              <div class="text-[11px] text-gray-400">Use an existing connection</div>
            </div>
          </button>
          <button
            type="button"
            :disabled="creatingFromConnection || uploadingCsv"
            class="flex items-center gap-2 rounded-lg border p-3 text-left transition-colors"
            :class="mode === 'csv'
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-950'
              : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'"
            @click="mode = 'csv'"
          >
            <UIcon name="heroicons-arrow-up-tray" class="h-5 w-5 text-gray-500 flex-shrink-0" />
            <div class="min-w-0">
              <div class="text-sm font-medium">Upload files</div>
              <div class="text-[11px] text-gray-400">CSV, Excel, Word, PDF — no database</div>
            </div>
          </button>
        </div>

        <!-- Upload CSV flow -->
        <div v-if="mode === 'csv'">
          <div class="mb-4">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Files <span class="text-red-500">*</span>
            </label>
            <input
              ref="csvInput"
              type="file"
              accept=".csv,.xlsx,.docx,.pdf,.pptx,text/csv"
              multiple
              class="block w-full text-sm text-gray-600 dark:text-gray-300 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 dark:file:bg-gray-800 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-gray-700 dark:file:text-gray-200 hover:file:bg-gray-200 dark:hover:file:bg-gray-700"
              :disabled="uploadingCsv"
              @change="onCsvFilesSelected"
            />
            <ul v-if="csvFiles.length" class="mt-2 space-y-1">
              <li
                v-for="(f, i) in csvFiles"
                :key="i"
                class="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400"
              >
                <UIcon name="heroicons-document-text" class="h-3.5 w-3.5 flex-shrink-0" />
                <span class="truncate">{{ f.name }}</span>
              </li>
            </ul>
            <p class="mt-1 text-[11px] text-gray-400">Drop CSV/Excel (→ tables), definitions (→ instructions), Word/PDF (→ knowledge). Sorted automatically.</p>
          </div>

          <div v-if="errorMessage" class="p-3 bg-red-50 dark:bg-red-950 text-red-700 rounded-lg text-sm mb-4">
            {{ errorMessage }}
          </div>

          <div class="flex justify-between items-center pt-4 border-t border-gray-100 dark:border-gray-800">
            <NuxtLink to="/agents" class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
              ← Cancel
            </NuxtLink>
            <UButton
              color="blue"
              size="xs"
              :loading="uploadingCsv"
              :disabled="!canSubmitCsv"
              @click="createAgentFromCsvFiles"
            >
              {{ uploadingCsv ? 'Uploading…' : 'Create & Continue' }}
            </UButton>
          </div>
        </div>

        <!-- Connection selector (multi-select for existing connections) -->
        <template v-else>
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
              <span v-else class="text-gray-400">Select connections</span>
            </template>
            <template #option="{ option }">
              <div class="flex items-center gap-2 w-full">
                <DataSourceIcon :type="option.type" :connector-key="option.connector_key" class="h-4 flex-shrink-0" />
                <div class="flex-1 min-w-0">
                  <div class="font-medium truncate">{{ option.name }}</div>
                  <div class="text-[10px] text-gray-400">
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

          <div v-if="errorMessage" class="p-3 bg-red-50 dark:bg-red-950 text-red-700 rounded-lg text-sm mb-4">
            {{ errorMessage }}
          </div>

          <div class="flex justify-between items-center pt-4 border-t border-gray-100 dark:border-gray-800">
            <NuxtLink to="/agents" class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
              ← Cancel
            </NuxtLink>
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
          <NuxtLink to="/agents" class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300">
            ← Cancel
          </NuxtLink>
        </div>
        </template>
      </div>

      <!-- Add Connection Modal -->
      <AddConnectionModal v-model="showAddConnectionModal" :skipSuccessStep="true" @created="handleNewConnectionCreated" />
    </div>
  </div>
</template>

<script setup lang="ts">
definePageMeta({ auth: true })
import { useCan } from '~/composables/usePermissions'
import Spinner from '~/components/Spinner.vue'
import WizardSteps from '@/components/datasources/WizardSteps.vue'
import AddConnectionModal from '~/components/AddConnectionModal.vue'

const route = useRoute()

interface Connection {
  id: string
  name: string
  type: string
  connector_key?: string | null
  table_count?: number
  tool_count?: number
  agent_count?: number
}

// Tool-provider connections (MCP / custom API) expose tools, not tables, so
// their catalog count is meaningless — show the tool count instead.
const TOOL_PROVIDER_TYPES = ['mcp', 'custom_api']
function connectionCountLabel(conn: Connection): string {
  if (TOOL_PROVIDER_TYPES.includes(conn.type)) {
    const n = conn.tool_count || 0
    return `${n} tool${n === 1 ? '' : 's'}`
  }
  const n = conn.table_count || 0
  return `${n} table${n === 1 ? '' : 's'}`
}

const connections = ref<Connection[]>([])
const loadingConnections = ref(true)
const selectedConnections = ref<Connection[]>([])
const agentName = ref('')
const useLlmSync = ref(true)
const creatingFromConnection = ref(false)
const errorMessage = ref('')
const showAddConnectionModal = ref(false)

// Source mode: connect an existing data source vs upload your own CSV files.
const mode = ref<'connection' | 'csv'>('connection')
const csvInput = ref<HTMLInputElement | null>(null)
const csvFiles = ref<File[]>([])
const uploadingCsv = ref(false)

function onCsvFilesSelected(e: Event) {
  const input = e.target as HTMLInputElement
  csvFiles.value = input.files ? Array.from(input.files) : []
  errorMessage.value = ''
}

const canSubmitCsv = computed(() => {
  return (
    agentName.value.trim().length > 0 &&
    csvFiles.value.length > 0 &&
    !uploadingCsv.value
  )
})

// Build a private file-agent from uploaded CSVs — no connection, no server paths.
// Members are allowed the `create_file_data_source` permission (DB types 403 for them).
async function createAgentFromCsvFiles() {
  if (!agentName.value.trim() || csvFiles.value.length === 0) return
  uploadingCsv.value = true
  errorMessage.value = ''

  try {
    // 1) Create the CSV data source (agent).
    const createRes = await useMyFetch('/data_sources', {
      method: 'POST',
      body: {
        name: agentName.value.trim(),
        type: 'csv',
        config: { file_paths: '' },
        is_public: false,
      },
    })

    if (createRes.error.value) {
      const err = createRes.error.value as any
      const status = err?.statusCode || err?.status || err?.response?.status
      if (status === 403) {
        errorMessage.value = "You don't have permission to create agents"
      } else {
        errorMessage.value = err?.data?.detail || 'Failed to create agent'
      }
      return
    }

    const ds = createRes.data.value as any
    if (!ds?.id) {
      errorMessage.value = 'Failed to create agent'
      return
    }

    // 2) Upload each CSV (one multipart request per file). Passing a FormData
    // body lets the browser set the multipart boundary — do NOT set Content-Type.
    for (const file of csvFiles.value) {
      const fd = new FormData()
      fd.append('file', file)
      const uploadRes = await useMyFetch(`/data_sources/${ds.id}/files`, {
        method: 'POST',
        body: fd,
      })
      if (uploadRes.error.value || !uploadRes.data.value) {
        errorMessage.value = `Failed to upload ${file.name}`
        return
      }
    }

    // 3) Tables populate server-side (async) — navigate to the agent's schema step.
    navigateTo(`/agents/new/${ds.id}/schema`)
  } catch (err: any) {
    errorMessage.value = err?.message || 'An error occurred'
  } finally {
    uploadingCsv.value = false
  }
}

async function handleNewConnectionCreated(connectionData: any) {
  // Refresh connections list
  await loadConnections()

  // Select the newly created connection — and ONLY it. Creating a connection
  // from this screen is an explicit "build the agent on this" action, but the
  // page auto-selects the single pre-existing connection on mount, so appending
  // silently produced a two-connection agent (observed: a Fabric agent that also
  // carried Power BI, showing both catalogs in its Select Tables step).
  if (connectionData?.id) {
    const newConn = connections.value.find(c => c.id === connectionData.id)
    if (newConn) {
      selectedConnections.value = [newConn]
    }
  }
}

const canSubmitExisting = computed(() => {
  return (
    selectedConnections.value.length > 0 &&
    agentName.value.trim().length > 0 &&
    !creatingFromConnection.value
  )
})

async function loadConnections() {
  loadingConnections.value = true
  try {
    const response = await useMyFetch('/connections', { method: 'GET' })
    const all = (response.data.value || []) as Connection[]
    // Only offer connections the user can actually build an agent on, so we
    // don't surface a connection that would be rejected on Save.
    connections.value = all.filter(c =>
      useCan('create_data_sources', { type: 'connection', id: (c as any).id })
    )
  } catch (err) {
    console.error('Failed to load connections:', err)
  } finally {
    loadingConnections.value = false
  }
}

async function createAgentFromExistingConnection() {
  if (selectedConnections.value.length === 0 || !agentName.value.trim()) return
  creatingFromConnection.value = true
  errorMessage.value = ''

  try {
    const payload = {
      name: agentName.value.trim(),
      connection_ids: selectedConnections.value.map(c => c.id),
      use_llm_sync: useLlmSync.value,
      is_public: false,
      generate_summary: false,
      generate_conversation_starters: false,
      generate_ai_rules: false,
    }

    const response = await useMyFetch('/data_sources', {
      method: 'POST',
      body: payload,
    })

    if (response.error.value) {
      const errData = (response.error.value as any).data as any
      errorMessage.value = errData?.detail || 'Failed to create agent'
      return
    }

    const result = response.data.value as any
    if (result?.id) {
      navigateTo(`/agents/new/${result.id}/schema`)
    } else {
      navigateTo('/agents')
    }
  } catch (err: any) {
    errorMessage.value = err?.message || 'An error occurred'
  } finally {
    creatingFromConnection.value = false
  }
}

onMounted(async () => {
  await loadConnections()

  const modeParam = String(route.query.mode || '')
  const forcedNew = modeParam === 'new_connection'
  const connectionParam = route.query.connection as string

  // Deep-link straight into the upload-files flow (from AddConnectionModal tile).
  if (modeParam === 'upload') {
    mode.value = 'csv'
    return
  }

  // Pre-select connection if passed via query param (from AddConnectionModal)
  if (connectionParam) {
    const matchingConn = connections.value.find(c => c.id === connectionParam)
    if (matchingConn) {
      selectedConnections.value = [matchingConn]
    }
  } else if (forcedNew) {
    // Explicitly asked to add a connection.
    showAddConnectionModal.value = true
  } else if (connections.value.length === 0) {
    // No connections the user can build on — default to the CSV upload path,
    // which any member can use without a database connection.
    mode.value = 'csv'
  } else if (connections.value.length === 1) {
    // Single connection - auto-select it
    selectedConnections.value = [connections.value[0]]
  }
})
</script>
