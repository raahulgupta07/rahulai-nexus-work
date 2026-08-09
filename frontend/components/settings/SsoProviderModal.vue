<!--
  SsoProviderModal — the SSO provider edit form, in a dialog.

  This is the inline edit form from pages/settings/identity-provider.vue moved
  into a modal. Same fields, same payload shape: Google routes through
  config.google (OAuth — no issuer/scopes/claims), everything else through an
  oidc_providers entry keyed by `name`.

  ★The client secret is WRITE-ONLY. The API returns `client_secret_set` and
  never the secret itself, so the input starts blank on every open and a blank
  value means "keep whatever is stored" — the payload simply omits the key.
  Pre-filling it with a placeholder value would save that placeholder.
-->
<template>
  <UModal v-model="isOpen" :ui="{ width: 'sm:max-w-2xl' }">
    <div class="bg-white dark:bg-gray-900 rounded-lg flex flex-col max-h-[85vh]">
      <!-- Header -->
      <div class="flex items-center gap-3 px-5 py-4 border-b border-gray-100 dark:border-gray-800">
        <ProviderMark :icon="provider?.icon" :label="provider?.label" :size="28" />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <h3 class="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
              {{ provider?.label || provider?.name }}
            </h3>
            <span
              v-if="provider?.type"
              class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 flex-shrink-0"
            >{{ provider.type }}</span>
          </div>
          <p class="text-[11px] text-gray-400 dark:text-gray-500">
            {{ provider?.configured ? $t('settings.identityProvider.ssoEdit') : $t('settings.identityProvider.ssoConfigure') }}
          </p>
        </div>
        <button
          type="button"
          class="text-gray-300 hover:text-gray-500 flex-shrink-0"
          :title="$t('settings.identityProvider.ssoCancel')"
          @click="close"
        >
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Body: one scrolling page, no rail, no tabs. -->
      <div ref="bodyRef" class="flex-1 overflow-y-auto px-5 py-4 space-y-6">
        <!-- ── Connection ────────────────────────────────────────────── -->
        <section class="space-y-3">
          <h4 class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
            {{ $t('settings.identityProvider.ssoSectionConnection') }}
          </h4>

          <!-- Issuer (OIDC only — Google is OAuth and has no issuer) -->
          <div v-if="!isGoogle">
            <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoIssuer') }}</label>
            <input
              v-model="form.issuer"
              type="text"
              placeholder="https://id.corp.com/realms/main"
              class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono"
            />
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoClientId') }}</label>
              <input
                v-model="form.client_id"
                type="text"
                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
              />
            </div>
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoClientSecret') }}</label>
              <input
                v-model="form.client_secret"
                type="password"
                autocomplete="new-password"
                :placeholder="secretSet ? $t('settings.identityProvider.ssoSecretSaved') : ''"
                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
              />
              <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{{ $t('settings.identityProvider.ssoSecretHint') }}</p>
            </div>
          </div>

          <!-- Read-only: what the admin must paste into the provider console. -->
          <div>
            <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoRedirectUri') }}</label>
            <div class="flex items-center gap-2">
              <code class="flex-1 min-w-0 truncate px-2 py-1.5 text-[11px] font-mono border border-gray-200 dark:border-gray-700 rounded bg-gray-50 dark:bg-gray-950 text-gray-600 dark:text-gray-300">{{ redirectUri }}</code>
              <button
                type="button"
                class="px-2 py-1.5 text-[11px] text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 flex-shrink-0"
                @click="copyRedirectUri"
              >
                {{ copied ? $t('settings.identityProvider.ssoCopied') : $t('settings.identityProvider.ssoCopy') }}
              </button>
            </div>
            <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{{ $t('settings.identityProvider.ssoRedirectUriHint') }}</p>
          </div>

          <!-- Scopes (OIDC only) -->
          <div v-if="!isGoogle">
            <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoScopes') }}</label>
            <input
              v-model="form.scopesText"
              type="text"
              placeholder="openid, email, profile"
              class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
            />
          </div>
        </section>

        <!-- ── Sign-in button (OIDC only — Google's button is fixed) ──── -->
        <section v-if="!isGoogle" class="space-y-3">
          <h4 class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
            {{ $t('settings.identityProvider.ssoSectionButton') }}
          </h4>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoLabel') }}</label>
              <input
                v-model="form.label"
                type="text"
                placeholder="Keycloak"
                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
              />
            </div>
            <!-- ★The mark was always saved (`icon` is a field on the provider)
                 but had no control, so it could only ever be whatever the
                 canonical row happened to carry. A self-hosted Keycloak fronting
                 a corporate directory should be able to show the right logo. -->
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoMark') }}</label>
              <div class="flex items-center gap-2">
                <ProviderMark :icon="form.icon" :label="form.label || form.name" />
                <select
                  v-model="form.icon"
                  class="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200"
                >
                  <option v-for="m in MARK_CHOICES" :key="m.value" :value="m.value">{{ m.text }}</option>
                </select>
              </div>
            </div>
          </div>
          <p class="mt-1 text-[10px] text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ssoMarkHint') }}</p>
        </section>

        <!-- ── Behaviour ─────────────────────────────────────────────── -->
        <section class="space-y-3">
          <h4 class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
            {{ $t('settings.identityProvider.ssoSectionBehaviour') }}
          </h4>

          <label class="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" v-model="form.enabled" class="rounded border-gray-300 dark:border-gray-600" />
            <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoEnableProvider') }}</span>
          </label>

          <!-- ★Admission, which is a different question from authentication.
               Signing in proves who somebody is; this decides whether that is
               enough to get an account. Off by default. -->
          <label class="flex items-start gap-2 cursor-pointer">
            <input type="checkbox" v-model="form.auto_provision" class="mt-0.5 rounded border-gray-300 dark:border-gray-600" />
            <span class="text-[11px] text-gray-600 dark:text-gray-300">
              {{ $t('settings.identityProvider.ssoAutoProvision') }}
              <span class="block text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ssoAutoProvisionHint') }}</span>
            </span>
          </label>

          <template v-if="!isGoogle">
            <div class="flex items-center flex-wrap gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="form.pkce" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoPkce') }}</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="form.discovery" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoDiscovery') }}</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="form.sync_groups" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoSyncGroups') }}</span>
              </label>
            </div>

            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoUidClaim') }}</label>
              <input
                v-model="form.uid_claim"
                type="text"
                placeholder="sub"
                class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono"
              />
              <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-1">{{ $t('settings.identityProvider.ssoUidClaimHint') }}</p>
            </div>

            <div v-if="form.sync_groups" class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoGroupClaim') }}</label>
                <input
                  v-model="form.group_claim"
                  type="text"
                  placeholder="groups"
                  class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono"
                />
              </div>
              <div class="flex items-end">
                <label class="flex items-center gap-2 cursor-pointer pb-1.5">
                  <input type="checkbox" v-model="form.resolve_group_names" class="rounded border-gray-300 dark:border-gray-600" />
                  <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoResolveGroupNames') }}</span>
                </label>
              </div>
            </div>
          </template>
        </section>
      </div>

      <!-- Footer -->
      <div class="flex items-center gap-2 px-5 py-3 border-t border-gray-100 dark:border-gray-800">
        <span v-if="error" class="text-[11px] text-red-500 truncate">{{ error }}</span>
        <span class="text-[11px] text-gray-400 dark:text-gray-500 ms-auto flex items-center gap-1 flex-shrink-0">
          <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>
          {{ $t('settings.identityProvider.ssoEncryptedNote') }}
        </span>
        <button
          type="button"
          class="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 flex-shrink-0"
          @click="close"
        >
          {{ $t('settings.identityProvider.ssoCancel') }}
        </button>
        <button
          type="button"
          class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 flex-shrink-0"
          :disabled="saving"
          @click="handleSave"
        >
          {{ saving ? $t('settings.identityProvider.ssoSaving') : $t('settings.identityProvider.ssoSave') }}
        </button>
      </div>
    </div>
  </UModal>
</template>

<script setup lang="ts">
import ProviderMark from '~/components/settings/ProviderMark.vue'
import { useSsoProviders, type SsoConfig } from '~/ee/composables/useSsoProviders'

const props = defineProps<{
  modelValue: boolean
  // The row being edited, as ssoRows builds it: name/canonical, label, icon,
  // type, isGoogle, configured, enabled.
  provider: any | null
  // The whole saved SSO config. A provider save is a PUT of the FULL providers
  // list, so the other entries have to be carried through untouched.
  config: SsoConfig | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved', config: SsoConfig): void
}>()

const isOpen = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

// A second composable instance is fine — it is stateless apart from its own
// loading/error refs, and the page refreshes off the `saved` payload.
const { saving, error, saveConfig } = useSsoProviders()

// The marks ProviderMark can draw. `generic` is the padlock fallback, offered
// deliberately so an admin can opt OUT of a wrong-looking logo rather than being
// stuck with whichever one the canonical row shipped with.
const MARK_CHOICES = [
  { value: 'keycloak', text: 'Keycloak' },
  { value: 'entra', text: 'Microsoft Entra ID' },
  { value: 'google', text: 'Google' },
  { value: 'oidc', text: 'OpenID Connect' },
  { value: 'okta', text: 'Okta' },
  { value: 'auth0', text: 'Auth0' },
  { value: 'generic', text: 'Generic' },
]

const isGoogle = computed(() => !!props.provider?.isGoogle)
const canonical = computed(() => props.provider?.canonical || props.provider?.name || '')

// Same shape as the page's ssoForm. `name` and `icon` are carried, not edited —
// they identify the entry and pick its chip.
const form = reactive({
  name: '',
  enabled: true,
  issuer: '',
  client_id: '',
  client_secret: '',
  scopesText: 'openid, email, profile',
  label: '',
  icon: '',
  pkce: true,
  discovery: true,
  uid_claim: 'sub',
  sync_groups: false,
  group_claim: 'groups',
  resolve_group_names: false,
  auto_provision: false,
})

const secretSet = ref(false)
const copied = ref(false)
const bodyRef = ref<HTMLElement | null>(null)

// Mirrors backend _get_redirect_uri: base URL + /api/auth/{provider}/callback.
const redirectUri = computed(() => {
  const name = canonical.value || 'oidc'
  const origin = process.client ? window.location.origin : ''
  return `${origin}/api/auth/${name}/callback`
})

const copyRedirectUri = async () => {
  try {
    await navigator.clipboard.writeText(redirectUri.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // Clipboard is permission-gated; the URI is visible and selectable anyway.
  }
}

// Hydrate from the saved config. Always blanks client_secret — see the header.
const hydrate = () => {
  const row = props.provider
  if (!row) return
  form.client_secret = ''
  error.value = null

  if (row.isGoogle) {
    const g = props.config?.google
    form.name = 'google'
    form.label = 'Google'
    form.icon = row.icon || 'google'
    form.enabled = !!g?.enabled
    form.client_id = g?.client_id || ''
    form.auto_provision = !!g?.auto_provision
    secretSet.value = !!g?.client_secret_set
    return
  }

  const p = props.config?.providers?.find((x) => x.name === canonical.value)
  if (p) {
    form.name = p.name
    form.label = p.label || row.label
    form.enabled = p.enabled
    form.issuer = p.issuer
    form.client_id = p.client_id
    form.scopesText = (p.scopes || []).join(', ')
    form.icon = p.icon || row.icon
    form.pkce = p.pkce
    form.discovery = p.discovery
    form.uid_claim = p.uid_claim
    form.sync_groups = p.sync_groups
    form.group_claim = p.group_claim
    form.resolve_group_names = p.resolve_group_names
    form.auto_provision = !!p.auto_provision
    secretSet.value = p.client_secret_set
  } else {
    // Nothing saved under this canonical name yet — the same minimal preset the
    // inline form used.
    form.name = canonical.value
    form.label = row.label
    form.enabled = true
    form.issuer = ''
    form.client_id = ''
    form.scopesText = 'openid, email, profile'
    form.icon = row.icon
    form.pkce = true
    form.discovery = true
    form.uid_claim = 'sub'
    form.sync_groups = false
    form.group_claim = 'groups'
    form.resolve_group_names = false
    form.auto_provision = false
    secretSet.value = false
  }
}

watch(
  () => [props.modelValue, props.provider],
  ([open]) => {
    if (!open) return
    hydrate()
    // Focus the first text field so the dialog owns the keyboard on open.
    // UModal already traps focus and handles Escape / backdrop dismissal.
    nextTick(() => {
      const first = bodyRef.value?.querySelector<HTMLInputElement>('input[type="text"]')
      first?.focus()
    })
  },
  { immediate: true },
)

const close = () => { isOpen.value = false }

// Drop client_secret_set from a read entry so it round-trips as a write entry;
// omitting client_secret keeps the stored one.
const serializeProviders = (list: any[]) => list.map((p) => {
  const { client_secret_set, ...rest } = p
  return { ...rest }
})

const handleSave = async () => {
  if (isGoogle.value) {
    const g: any = {
      enabled: form.enabled,
      client_id: form.client_id,
      auto_provision: form.auto_provision,
    }
    if (form.client_secret) g.client_secret = form.client_secret
    const saved = await saveConfig({ google: g })
    if (saved) { emit('saved', saved); close() }
    return
  }

  const entry: any = {
    name: form.name,
    enabled: form.enabled,
    issuer: form.issuer,
    client_id: form.client_id,
    scopes: form.scopesText.split(',').map((s) => s.trim()).filter(Boolean),
    label: form.label,
    icon: form.icon,
    pkce: form.pkce,
    discovery: form.discovery,
    uid_claim: form.uid_claim,
    sync_groups: form.sync_groups,
    group_claim: form.group_claim,
    resolve_group_names: form.resolve_group_names,
    auto_provision: form.auto_provision,
  }
  if (form.client_secret) entry.client_secret = form.client_secret

  const existing = props.config?.providers || []
  const providers = serializeProviders(existing).map((p) => (p.name === entry.name ? entry : p))
  if (!existing.find((p) => p.name === entry.name)) providers.push(entry)

  const saved = await saveConfig({ providers })
  if (saved) { emit('saved', saved); close() }
}
</script>
