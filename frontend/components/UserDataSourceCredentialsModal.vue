<template>
  <UModal v-model="open" :ui="{ width: 'sm:max-w-lg' }">
    <div class="p-5 relative">
      <button @click="emit('update:modelValue', false)" class="absolute top-2 end-2 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 outline-none">
        <Icon name="heroicons:x-mark" class="w-5 h-5" />
      </button>

      <div class="mb-4">
        <h1 class="text-base font-semibold flex items-center">
            <DataSourceIcon :type="connectionType" class="h-4 me-2" />
            {{ $t('data.connectNamed', { name: ds?.name }) }}</h1>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ $t('data.provideCredentials') }}</p>
      </div>

      <div v-if="authOptions.length > 1" class="mb-3">
        <label class="text-xs text-gray-600 dark:text-gray-400">{{ $t('data.authMethod') }}</label>
        <USelectMenu v-model="authMode" :options="authOptions" option-attribute="label" value-attribute="value" />
      </div>

      <!-- User sign-in mode (Power BI user_login): email/password → MFA device → tenant → done -->
      <div v-if="isUserLoginMode" class="mt-4">
        <!-- Step 1: email + password form -->
        <template v-if="ulStep === 'form'">
          <div class="space-y-3">
            <div class="flex flex-col">
              <label class="text-xs text-gray-600 dark:text-gray-400 mb-1">{{ $t('data.email') }}</label>
              <input
                type="email"
                v-model="ul.email"
                autocomplete="username"
                placeholder="you@company.com"
                class="border rounded px-2 py-1 text-sm"
                @keyup.enter="onUserLoginConnect"
              />
            </div>
            <div class="flex flex-col">
              <label class="text-xs text-gray-600 dark:text-gray-400 mb-1">{{ $t('data.password') }}</label>
              <input
                type="password"
                v-model="ul.password"
                autocomplete="current-password"
                class="border rounded px-2 py-1 text-sm"
                @keyup.enter="onUserLoginConnect"
              />
            </div>
          </div>

          <p class="mt-3 text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
            {{ $t('data.userLoginPrivacy') }}
          </p>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
            We connect every Microsoft tenant you can access — no need to pick one.
          </p>

          <div class="flex justify-end mt-5">
            <div class="space-x-2">
              <UButton size="xs" color="gray" variant="soft" @click="emit('update:modelValue', false)">{{ $t('data.cancel') }}</UButton>
              <UButton
                size="xs"
                color="blue"
                variant="solid"
                :loading="ulLoading"
                :disabled="!ul.email || !ul.password"
                @click="onUserLoginConnect"
              >
                {{ $t('data.connectWithMyAccount') }}
              </UButton>
            </div>
          </div>
        </template>

        <!-- Step 2: MFA device-code card -->
        <template v-else-if="ulStep === 'device'">
          <div class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-xs font-medium">
            <Icon name="heroicons:shield-check" class="w-4 h-4" />
            {{ $t('data.userLoginMfaDetected') }}
          </div>

          <div class="mt-4 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
            <p class="text-xs text-gray-500 dark:text-gray-400 mb-2">{{ $t('data.userLoginEnterCode') }}</p>
            <div class="flex items-center gap-2">
              <span class="font-mono text-2xl tracking-widest select-all text-gray-900 dark:text-gray-100">{{ ul.userCode }}</span>
              <UButton size="2xs" color="gray" variant="soft" :icon="ulCopied ? 'heroicons:check' : 'heroicons:clipboard-document'" @click="copyUserCode">
                {{ ulCopied ? $t('data.copied') : $t('data.copy') }}
              </UButton>
            </div>
            <a
              v-if="ul.verificationUri"
              :href="ul.verificationUri"
              target="_blank"
              rel="noopener noreferrer"
              class="inline-flex items-center gap-1 mt-3 text-xs text-blue-600 dark:text-blue-400 hover:underline"
            >
              {{ $t('data.userLoginOpenVerification') }}
              <Icon name="heroicons:arrow-top-right-on-square" class="w-3.5 h-3.5" />
            </a>
          </div>

          <div class="flex items-center gap-2 mt-4 text-xs text-gray-500 dark:text-gray-400">
            <Icon name="heroicons:arrow-path" class="w-4 h-4 animate-spin" />
            {{ $t('data.userLoginWaiting') }}
          </div>
        </template>

        <!-- Step 3 (tenant picker) removed — the backend now auto-merges every
             tenant the user can access, so there is nothing to pick. -->

        <!-- Step 4: done -->
        <template v-else-if="ulStep === 'done'">
          <!-- Fabric federated sync: live progress while every endpoint is pulled -->
          <div v-if="isFabric && syncProgress && syncProgress.status === 'syncing'" class="space-y-2">
            <div class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
              <Icon name="heroicons:arrow-path" class="w-4 h-4 animate-spin" />
              <span v-if="syncProgress.phase === 'discovering'">Discovering your Fabric workspaces…</span>
              <span v-else>Syncing endpoints {{ syncProgress.endpoints_done }} / {{ syncProgress.endpoints_total }}…</span>
            </div>
            <div class="h-1.5 w-full rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div class="h-full bg-blue-500 transition-all duration-500" :style="{ width: syncPct + '%' }"></div>
            </div>
            <div v-if="syncProgress.tables" class="text-xs text-gray-500">{{ syncProgress.tables }} tables so far</div>
          </div>
          <div v-else-if="isFabric && syncProgress && syncProgress.status === 'error'" class="flex items-center gap-2 text-sm text-red-600">
            <Icon name="heroicons:exclamation-triangle" class="w-5 h-5" />
            Sign-in worked, but sync failed: {{ syncProgress.error || 'unknown error' }}
          </div>
          <div v-else class="flex items-center gap-2 text-sm text-green-600">
            <Icon name="heroicons:check-circle" class="w-5 h-5" />
            <span v-if="isFabric && syncProgress && syncProgress.tables">Connected — synced {{ syncProgress.tables }} tables across all your workspaces</span>
            <span v-else>{{ $t('data.connectedSuccess') }}</span>
          </div>
        </template>

        <div v-if="ulError" class="mt-3 text-xs text-red-600">{{ ulError }}</div>
      </div>

      <!-- OAuth mode: standard sign-in button -->
      <div v-else-if="isOAuthMode" class="mt-4">
        <UButton
          size="sm"
          color="blue"
          variant="solid"
          block
          :loading="oauthLoading"
          @click="onOAuthSignIn"
        >
          {{ currentAuthTitle || $t('data.signIn') }}
        </UButton>
        <!-- Multi-tenant Power BI: reassure that a single sign-in covers every
             tenant the user belongs to (discovery + merge happens server-side
             in the OAuth callback). Gated so every other connector is unchanged. -->
        <p v-if="isPowerBiMt" class="mt-2 text-xs text-gray-500 dark:text-gray-400 leading-relaxed">
          One sign-in covers every Power BI tenant you belong to — we'll discover and merge them automatically.
        </p>
      </div>

      <!-- Standard credential form -->
      <template v-else>
        <div class="space-y-3">
          <div v-for="field in credentialFields" :key="field.key" class="flex flex-col">
            <label class="text-xs text-gray-600 dark:text-gray-400 mb-1">{{ field.title }}</label>
            <input v-if="field.type === 'string'" :type="field.format === 'password' ? 'password' : 'text'" v-model="form.credentials[field.key]" class="border rounded px-2 py-1 text-sm" />
            <input v-else-if="field.type === 'integer'" type="number" v-model.number="form.credentials[field.key]" class="border rounded px-2 py-1 text-sm" />
            <UCheckbox v-else-if="field.type === 'boolean'" v-model="form.credentials[field.key]">{{ field.title }}</UCheckbox>
            <textarea v-else-if="field.type === 'text' || field.type === 'textarea'" v-model="form.credentials[field.key]" class="border rounded px-2 py-1 text-sm"></textarea>
            <input v-else v-model="form.credentials[field.key]" class="border rounded px-2 py-1 text-sm" />
          </div>
        </div>

        <div class="flex justify-between mt-5">
          <UButton size="xs" color="gray" variant="soft" :loading="testing" @click="onTest">{{ $t('data.testConnection') }}</UButton>
          <div class="space-x-2">
            <UButton size="xs" color="gray" variant="soft" @click="emit('update:modelValue', false)">{{ $t('data.cancel') }}</UButton>
            <UButton size="xs" color="blue" variant="solid" :loading="saving" @click="onSave">{{ $t('data.save') }}</UButton>
          </div>
        </div>
      </template>

      <div v-if="testResult" class="mt-3 text-xs">
        <span :class="testResult.success ? 'text-green-600' : 'text-red-600'">
          {{ testResult.success ? $t('data.connectedSuccess') : $t('data.connectionFailed') }}
        </span>
        <span v-if="testResult.message" class="text-gray-500 dark:text-gray-400"> - {{ testResult.message }}</span>
      </div>
    </div>
  </UModal>

</template>

<script lang="ts" setup>
import { computed, watch, ref, onBeforeUnmount } from 'vue'

const props = defineProps<{ modelValue: boolean, dataSource: any }>()
const emit = defineEmits(['update:modelValue', 'saved'])

const { t } = useI18n()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v)
})

const ds = computed(() => props.dataSource)
// Per-user sign-in endpoints differ by connector: fabric_user posts to
// /fabric-signin/... (single Fabric SQL endpoint), everything else (powerbi_user)
// posts to /user-signin/... . Resolve the connector type from the data source or
// its first connection so the same modal drives both.
const signinBase = computed(() => {
  const t = ds.value?.type || ds.value?.connections?.[0]?.type
  return t === 'fabric_user' ? 'fabric-signin' : 'user-signin'
})
// Use nested connection type (Option A architecture)
const connectionType = computed(() => ds.value?.connection?.type || ds.value?.type)
const connectionId = computed(() => ds.value?.connection?.id || ds.value?.connection_id || ds.value?.connections?.[0]?.id)
const authMode = ref<string>('')
const form = ref<{ auth_mode: string, credentials: Record<string, any>, is_primary?: boolean }>({ auth_mode: '', credentials: {}, is_primary: true })
const authOptions = ref<{ label: string, value: string }[]>([])
const fieldsByAuth = ref<Record<string, any>>({})
const credentialFields = computed(() => {
  const schema = fieldsByAuth.value[authMode.value]
  if (!schema) return []
  const props = (schema.properties || {})
  const req = schema.required || []
  return Object.keys(props).map((k) => {
    const f = props[k] || {}
    return {
      key: k,
      title: f.title || k,
      type: f.type || 'string',
      format: f.format,
      required: req.includes(k)
    }
  })
})

watch(open, async (val) => {
  if (val && connectionType.value) {
    await loadFields()
  }
})

async function loadFields() {
  const { data } = await useMyFetch(`/data_sources/${connectionType.value}/fields`, { method: 'GET', query: { auth_policy: 'user_required' } })
  const payload = data.value as any
  const byAuth = (payload?.credentials_by_auth) || {}
  fieldsByAuth.value = byAuth
  catalogOwnership.value = payload?.catalog_ownership || 'shared'
  // build options
  const names = Object.keys((payload?.auth?.by_auth) || {})
  authOptions.value = names.map((n) => ({ label: payload.auth.by_auth[n]?.title || n, value: n }))
  // The OAuth-only direct-redirect path is handled by `useConnectionSignIn`
  // before this modal ever opens. By the time we're here, the user actually
  // has a choice to make — render the standard form with the registry's
  // default auth selected.
  const defaultAuth = payload?.auth?.default
  authMode.value = (defaultAuth && names.includes(defaultAuth)) ? defaultAuth : names[0] || ''
  form.value.auth_mode = authMode.value
  form.value.credentials = {}
}

const catalogOwnership = ref<string>('shared')

watch(authMode, (v) => {
  form.value.auth_mode = v || ''
  form.value.credentials = {}
})

const isOAuthMode = computed(() => authMode.value === 'oauth')

// Multi-tenant Power BI connector (`powerbi_mt`) — reuses the standard OAuth
// sign-in, but the callback scans every tenant the user belongs to and merges
// their tables. Used only to surface reassuring copy; every other connector is
// byte-identical.
const isPowerBiMt = computed(() =>
  connectionType.value === 'powerbi_mt' || (ds.value as any)?.connector_key === 'powerbi_mt'
)

// ── Power BI user sign-in mode (auth variant 'user_login') ────────────────────
const isUserLoginMode = computed(() => authMode.value === 'user_login')
type UlStep = 'form' | 'device' | 'tenants' | 'done'
const ulStep = ref<UlStep>('form')
const ul = ref<{
  email: string,
  password: string,
  userCode: string,
  verificationUri: string,
  deviceCode: string,
  interval: number,
  tenants: { id: string, name?: string, domain?: string }[],
  selectedTenant: string,
}>({ email: '', password: '', userCode: '', verificationUri: '', deviceCode: '', interval: 5, tenants: [], selectedTenant: '' })
const ulLoading = ref(false)
const ulError = ref('')
const ulCopied = ref(false)
let ulPollTimer: ReturnType<typeof setInterval> | null = null

function stopUlPolling() {
  if (ulPollTimer) {
    clearInterval(ulPollTimer)
    ulPollTimer = null
  }
}

function resetUserLogin() {
  stopUlPolling()
  ulStep.value = 'form'
  ulLoading.value = false
  ulError.value = ''
  ulCopied.value = false
  ul.value = { email: '', password: '', userCode: '', verificationUri: '', deviceCode: '', interval: 5, tenants: [], selectedTenant: '' }
}

function truncateId(id: string) {
  if (!id) return ''
  return id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id
}

async function copyUserCode() {
  try {
    await navigator.clipboard.writeText(ul.value.userCode)
    ulCopied.value = true
    setTimeout(() => { ulCopied.value = false }, 1500)
  } catch (e) {
    // clipboard unavailable — user can still read the code
  }
}

// The backend now auto-merges ALL tenants the user can access server-side, so
// there is never a tenant to pick — always finish. (`onSelectTenant` and the
// 'tenants' template block are kept dormant but are now unreachable.)
function handleConnected(result: any) {
  // Fabric federated sign-in kicks off a background multi-endpoint sync — stay
  // on the done step and show live progress instead of closing immediately.
  if (isFabric.value && result?.sync === 'started') {
    stopUlPolling()
    ulStep.value = 'done'
    startSyncPolling()
    return
  }
  finishUserLogin()
}

function finishUserLogin() {
  stopUlPolling()
  ulStep.value = 'done'
  emit('saved')
  emit('update:modelValue', false)
}

// --- Fabric federated background-sync progress -----------------------------
const isFabric = computed(() => signinBase.value === 'fabric-signin')
const syncProgress = ref<any>(null)
let syncPollTimer: any = null

const syncPct = computed(() => {
  const p = syncProgress.value
  if (!p) return 0
  if (p.status === 'done') return 100
  if (p.phase === 'discovering') return 8
  const tot = Number(p.endpoints_total) || 0
  if (!tot) return 12
  return Math.min(96, 12 + Math.round((Number(p.endpoints_done) / tot) * 84))
})

function stopSyncPolling() {
  if (syncPollTimer) { clearInterval(syncPollTimer); syncPollTimer = null }
}

function startSyncPolling() {
  stopSyncPolling()
  syncProgress.value = { status: 'syncing', phase: 'discovering', endpoints_done: 0, endpoints_total: 0, tables: 0 }
  syncPollTimer = setInterval(pollSyncStatus, 1500)
}

async function pollSyncStatus() {
  try {
    const { data, error } = await useMyFetch(`/data_sources/${ds.value.id}/fabric-signin/sync-status`, { method: 'GET' })
    if (error.value) throw error.value
    const p = data.value as any
    if (p) syncProgress.value = p
    if (p && (p.status === 'done' || p.status === 'error' || p.status === 'idle')) {
      stopSyncPolling()
      emit('saved')  // overlay is ready — refresh the parent's table list
    }
  } catch (e) {
    // transient — keep polling; a terminal error state comes from the endpoint itself
  }
}

async function onUserLoginConnect() {
  if (!ul.value.email || !ul.value.password) return
  ulError.value = ''
  ulLoading.value = true
  try {
    // No tenant_id: the server discovers and merges every tenant automatically.
    const body: Record<string, any> = { email: ul.value.email, password: ul.value.password }
    const { data, error } = await useMyFetch(`/data_sources/${ds.value.id}/${signinBase.value}/connect`, { method: 'POST', body })
    if (error.value) throw error.value
    const result = data.value as any
    if (result?.status === 'mfa_required') {
      ul.value.userCode = result.user_code || ''
      ul.value.verificationUri = result.verification_uri || ''
      ul.value.deviceCode = result.device_code || ''
      ul.value.interval = Number(result.interval) || 5
      ulStep.value = 'device'
      startUlPolling()
    } else if (result?.status === 'connected') {
      handleConnected(result)
    } else {
      ulError.value = t('data.connectionFailed')
    }
  } catch (e: any) {
    ulError.value = e?.data?.detail || e?.message || t('data.connectionFailed')
  } finally {
    ulLoading.value = false
  }
}

function startUlPolling() {
  stopUlPolling()
  const wait = Math.max(ul.value.interval || 5, 5) * 1000
  ulPollTimer = setInterval(pollDeviceCode, wait)
}

async function pollDeviceCode() {
  try {
    // No tenant_id: the server discovers and merges every tenant automatically.
    const body: Record<string, any> = { device_code: ul.value.deviceCode }
    const { data, error } = await useMyFetch(`/data_sources/${ds.value.id}/${signinBase.value}/device-code/poll`, { method: 'POST', body })
    if (error.value) throw error.value
    const result = data.value as any
    if (result?.status === 'connected') {
      handleConnected(result)
    }
    // status 'pending' → keep waiting
  } catch (e: any) {
    // expired / declined / error → back to the form with a message
    stopUlPolling()
    ulStep.value = 'form'
    ulError.value = e?.data?.detail || e?.message || t('data.userLoginDeviceExpired')
  }
}

async function onSelectTenant() {
  if (!ul.value.selectedTenant) return
  ulError.value = ''
  ulLoading.value = true
  try {
    const { error } = await useMyFetch(`/data_sources/${ds.value.id}/user-signin/select-tenant`, { method: 'POST', body: { tenant_id: ul.value.selectedTenant } })
    if (error.value) throw error.value
    finishUserLogin()
  } catch (e: any) {
    ulError.value = e?.data?.detail || e?.message || t('data.connectionFailed')
  } finally {
    ulLoading.value = false
  }
}

// Reset the user-login flow whenever the modal closes or the auth mode changes.
watch(open, (val) => { if (!val) resetUserLogin() })
watch(authMode, () => { resetUserLogin() })
onBeforeUnmount(() => { stopUlPolling(); stopSyncPolling() })
const currentAuthTitle = computed(() => {
  const opt = authOptions.value.find(o => o.value === authMode.value)
  return opt?.label || t('data.signIn')
})
const oauthLoading = ref(false)

async function onOAuthSignIn() {
  if (!connectionId.value) {
    testResult.value = { success: false, message: t('data.noConnectionForSource') }
    return
  }
  try {
    oauthLoading.value = true
    const { data, error } = await useMyFetch(`/connections/${connectionId.value}/oauth/authorize`, { method: 'GET' })
    if (error.value) throw error.value
    const result = data.value as any
    if (result?.authorization_url) {
      // For powerbi_mt, leave a client-side breadcrumb so the post-callback
      // landing (/agents?oauth=success) can show a "tenants scanned & merged"
      // note. No backend coupling; cleared on read.
      if (isPowerBiMt.value) { try { sessionStorage.setItem('pbimt.oauthPending', '1') } catch (e) {} }
      window.location.href = result.authorization_url
    }
  } catch (e: any) {
    testResult.value = { success: false, message: e?.message || t('data.oauthStartFailed') }
  } finally {
    oauthLoading.value = false
  }
}

const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ success: boolean, message?: string } | null>(null)

async function onSave() {
  try {
    saving.value = true
    const { error } = await useMyFetch(`/data_sources/${ds.value.id}/my-credentials`, { method: 'POST', body: form.value })
    if (error.value) throw error.value
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    // optionally toast
  } finally {
    saving.value = false
  }
}

async function onTest() {
  try {
    testing.value = true
    const { data, error } = await useMyFetch(`/data_sources/${ds.value.id}/my-credentials/test`, { method: 'POST', body: form.value })
    if (error.value) throw error.value
    testResult.value = data.value as any
  } catch (e: any) {
    testResult.value = { success: false, message: e?.message || t('data.failed') }
  } finally {
    testing.value = false
  }
}

</script>


