<template>
  <div class="mt-4 space-y-4">

    <!-- ================================================================== -->
    <!-- Single Sign-On (SSO) Section (collapsible)                          -->
    <!-- ================================================================== -->
    <div class="border border-gray-200 dark:border-gray-700 rounded-lg">
      <button
        class="w-full flex items-center justify-between px-4 py-3 text-left"
        @click="ssoOpen = !ssoOpen"
      >
        <div>
          <h2 class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.identityProvider.ssoTitle') }}</h2>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('settings.identityProvider.ssoSubtitle') }}</p>
        </div>
        <svg class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0" :class="ssoOpen ? 'rotate-180' : ''" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div v-show="ssoOpen" class="px-4 pb-4 border-t border-gray-100 dark:border-gray-800 pt-4">

        <!-- Auth mode selector -->
        <p class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ $t('settings.identityProvider.ssoAuthMode') }}</p>
        <div class="grid grid-cols-3 gap-2 mb-4">
          <label
            v-for="mode in ssoAuthModes"
            :key="mode.value"
            class="flex flex-col gap-0.5 rounded border px-3 py-2 cursor-pointer transition-colors"
            :class="ssoAuthMode === mode.value
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/40'
              : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'"
          >
            <div class="flex items-center gap-2">
              <input
                type="radio"
                name="sso-auth-mode"
                :value="mode.value"
                :checked="ssoAuthMode === mode.value"
                class="text-blue-600 focus:ring-blue-500"
                @change="handleSetAuthMode(mode.value)"
              />
              <span class="text-xs font-medium text-gray-700 dark:text-gray-200">{{ mode.label }}</span>
            </div>
            <span class="text-[10px] text-gray-400 dark:text-gray-500 ms-6">{{ mode.hint }}</span>
          </label>
        </div>

        <!-- Provider list — fixed, always-visible set of the 4 supported providers -->
        <div class="flex items-center justify-between mb-2">
          <p class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ssoProviders') }}</p>
        </div>

        <div class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden mb-4">
          <div
            v-for="(row, idx) in ssoRows"
            :key="row.key"
            class="flex items-center gap-3 px-3 py-2.5 text-xs"
            :class="{ 'border-t border-gray-100 dark:border-gray-800': idx > 0 }"
          >
            <!-- Brand mark; falls back to the letter chip for an unknown icon. -->
            <SettingsProviderMark :icon="row.icon" :label="row.label || row.name" />
            <!-- Name + issuer -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-medium text-gray-700 dark:text-gray-200 truncate">{{ row.label || row.name }}</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 flex-shrink-0">{{ row.type }}</span>
              </div>
              <span v-if="row.issuer" class="block truncate font-mono text-[11px] text-gray-400 dark:text-gray-500">{{ row.issuer }}</span>
            </div>
            <!-- Not-configured badge (missing essential config: google=client_id+secret, oidc=issuer+client_id) -->
            <span
              v-if="!row.configured"
              class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 dark:bg-amber-950 text-amber-600 dark:text-amber-400 flex-shrink-0"
            >Not configured</span>
            <!-- Status pill -->
            <span
              class="px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0"
              :class="row.enabled ? 'bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-800 text-gray-400'"
            >
              {{ row.enabled ? $t('settings.identityProvider.ssoEnabled') : $t('settings.identityProvider.ssoDisabled') }}
            </span>
            <!-- ★Configure / Edit opens SsoProviderModal. The form used to be an
                 inline panel below this list; a dialog keeps the row list intact
                 while editing and leaves room for the redirect-URI field. -->
            <button class="text-[11px] text-blue-600 hover:text-blue-700 flex-shrink-0" @click="handleEditProvider(row)">{{ row.configured ? $t('settings.identityProvider.ssoEdit') : $t('settings.identityProvider.ssoConfigure') }}</button>
            <!-- Enable toggle -->
            <button
              type="button"
              role="switch"
              :aria-checked="row.enabled"
              class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors focus:outline-none"
              :class="row.enabled ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'"
              @click="handleToggleProvider(row)"
            >
              <span
                class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5"
                :class="row.enabled ? 'translate-x-4' : 'translate-x-0.5'"
              ></span>
            </button>
          </div>
          <div v-if="!ssoRows.length" class="px-3 py-6 text-center text-[11px] text-gray-400">
            {{ $t('settings.identityProvider.ssoNoProviders') }}
          </div>
        </div>

        <!-- Footer: saved flash + encrypted-at-rest note -->
        <div class="flex items-center gap-3">
          <span v-if="ssoSavedFlash" class="text-[11px] text-green-600">{{ $t('settings.identityProvider.ssoSaved') }}</span>
          <span v-if="ssoError" class="text-[11px] text-red-500">{{ ssoError }}</span>
          <span class="text-[11px] text-gray-400 dark:text-gray-500 ms-auto flex items-center gap-1">
            <svg class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>
            {{ $t('settings.identityProvider.ssoEncryptedNote') }}
          </span>
        </div>
      </div>
    </div>

    <!-- ================================================================== -->
    <!-- Entra ID Profile Sync Section                                       -->
    <!-- ================================================================== -->
    <ProfileSyncSection
      i18n-prefix="entra"
      endpoint="/api/organization/identity/entra-profile-sync"
      :allowed-fields="ENTRA_PROFILE_FIELDS"
      :default-visible="ENTRA_DEFAULT_VISIBLE"
    />

    <!-- ================================================================== -->
    <!-- Google Profile Sync Section                                         -->
    <!-- ================================================================== -->
    <ProfileSyncSection
      i18n-prefix="google"
      endpoint="/api/organization/identity/google-profile-sync"
      :allowed-fields="GOOGLE_PROFILE_FIELDS"
      :default-visible="GOOGLE_DEFAULT_VISIBLE"
    />

    <!-- ================================================================== -->
    <!-- SCIM Provisioning Section (collapsible)                             -->
    <!-- ================================================================== -->
    <div class="border border-gray-200 dark:border-gray-700 rounded-lg">
      <button
        class="w-full flex items-center justify-between px-4 py-3 text-left"
        @click="scimOpen = !scimOpen"
      >
        <div>
          <h2 class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.identityProvider.scimTitle') }}</h2>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('settings.identityProvider.scimSubtitle') }}</p>
        </div>
        <svg class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0" :class="scimOpen ? 'rotate-180' : ''" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div v-show="scimOpen" class="px-4 pb-4 border-t border-gray-100 dark:border-gray-800 pt-4">
      <!-- Enterprise Gate for SCIM -->
      <template v-if="!hasFeature('scim')">
        <div class="rounded border border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-900">
          <p class="text-xs text-gray-600 dark:text-gray-400 mb-2">
            {{ $t('settings.identityProvider.enterpriseScim') }}
          </p>
          <a
            href="#"
            target="_blank"
            rel="noopener noreferrer"
            class="text-xs text-blue-600 hover:text-blue-700"
          >
            {{ $t('settings.identityProvider.learnMore') }}
          </a>
        </div>
      </template>

      <template v-else>
        <!-- SCIM Endpoint URL -->
        <div class="mb-4 rounded border border-gray-200 dark:border-gray-700 p-3">
          <label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">{{ $t('settings.identityProvider.scimBaseUrl') }}</label>
          <div class="flex items-center gap-2">
            <code class="flex-1 text-xs bg-gray-50 dark:bg-gray-900 px-2 py-1.5 rounded border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 font-mono">
              {{ scimBaseUrl }}
            </code>
            <button
              class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300"
              @click="copyToClipboard(scimBaseUrl)"
            >
              {{ copied === 'url' ? $t('settings.identityProvider.copied') : $t('settings.identityProvider.copy') }}
            </button>
          </div>
          <p class="text-[11px] text-gray-400 dark:text-gray-400 mt-1">{{ $t('settings.identityProvider.scimBaseUrlHint') }}</p>
        </div>

        <!-- Token Management -->
        <div class="mb-3 flex items-center justify-between">
          <label class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.bearerTokens') }}</label>
          <button
            class="px-2 py-1 text-xs text-white bg-blue-600 rounded hover:bg-blue-700"
            @click="showCreateModal = true"
          >
            {{ $t('settings.identityProvider.generateToken') }}
          </button>
        </div>

        <!-- Loading State -->
        <div v-if="scimLoading" class="py-8 text-center">
          <div class="inline-block w-4 h-4 border-2 border-gray-200 dark:border-gray-700 border-t-gray-500 rounded-full animate-spin"></div>
        </div>

        <!-- Error State -->
        <div v-else-if="scimError" class="py-6 text-center text-xs text-red-500">
          {{ scimError }}
        </div>

        <!-- Tokens List -->
        <div v-else class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden">
          <template v-if="tokens.length > 0">
            <div
              v-for="(token, idx) in tokens"
              :key="token.id"
              class="flex items-center px-3 py-2.5 text-xs"
              :class="{ 'border-t border-gray-100 dark:border-gray-800': idx > 0 }"
            >
              <span class="w-36 flex-shrink-0 text-gray-700 dark:text-gray-300 font-medium truncate">{{ token.name }}</span>
              <span class="w-36 flex-shrink-0 text-gray-400 dark:text-gray-400 font-mono text-[11px]">{{ token.token_prefix }}...</span>
              <span class="flex-1 text-gray-400 dark:text-gray-400 text-[11px]">
                <template v-if="token.last_used_at">
                  {{ $t('settings.identityProvider.lastUsed', { when: formatRelativeTime(token.last_used_at) }) }}
                </template>
                <template v-else>
                  {{ $t('settings.identityProvider.neverUsed') }}
                </template>
              </span>
              <span class="w-24 flex-shrink-0 text-gray-400 dark:text-gray-400 text-[11px]">
                {{ formatRelativeTime(token.created_at) }}
              </span>
              <button
                class="text-[11px] text-red-500 hover:text-red-700 ms-2"
                @click="confirmRevoke(token)"
              >
                {{ $t('settings.identityProvider.revoke') }}
              </button>
            </div>
          </template>

          <!-- Empty State -->
          <div v-else class="py-8 text-center">
            <p class="text-xs text-gray-400 dark:text-gray-400">{{ $t('settings.identityProvider.noTokens') }}</p>
            <p class="text-[11px] text-gray-400 dark:text-gray-400 mt-1">{{ $t('settings.identityProvider.noTokensHint') }}</p>
          </div>
        </div>
      </template>
      </div>
    </div>

    <!-- ================================================================== -->
    <!-- LDAP Directory Sync Section (collapsible)                          -->
    <!-- ================================================================== -->
    <div class="border border-gray-200 dark:border-gray-700 rounded-lg">
      <button
        class="w-full flex items-center justify-between px-4 py-3 text-left"
        @click="ldapOpen = !ldapOpen"
      >
        <div>
          <h2 class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.identityProvider.ldapTitle') }}</h2>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('settings.identityProvider.ldapSubtitle') }}</p>
        </div>
        <svg class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0" :class="ldapOpen ? 'rotate-180' : ''" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div v-show="ldapOpen" class="px-4 pb-4 border-t border-gray-100 dark:border-gray-800 pt-4">
      <!-- Enterprise Gate for LDAP -->
      <template v-if="!hasFeature('ldap')">
        <div class="rounded border border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-900">
          <p class="text-xs text-gray-600 dark:text-gray-400 mb-2">
            {{ $t('settings.identityProvider.enterpriseLdap') }}
          </p>
          <a
            href="#"
            target="_blank"
            rel="noopener noreferrer"
            class="text-xs text-blue-600 hover:text-blue-700"
          >
            {{ $t('settings.identityProvider.learnMore') }}
          </a>
        </div>
      </template>

      <template v-else>
        <!-- ★One row, same visual language as the SSO providers above. The ~20
             config fields used to expand inline here; they now live in
             LdapConfigModal, which owns the save/test/sync actions too. -->
        <div class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden">
          <div class="flex items-center gap-3 px-3 py-2.5 text-xs">
            <SettingsProviderMark icon="ldap" label="LDAP" />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-medium text-gray-700 dark:text-gray-200 truncate">{{ $t('settings.identityProvider.ldapTitle') }}</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 flex-shrink-0">LDAP</span>
              </div>
              <span v-if="ldapSummary" class="block truncate font-mono text-[11px] text-gray-400 dark:text-gray-500">{{ ldapSummary }}</span>
            </div>
            <span
              class="px-1.5 py-0.5 rounded text-[10px] font-medium flex-shrink-0"
              :class="ldapConfig?.enabled ? 'bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-800 text-gray-400'"
            >
              {{ ldapConfig?.enabled ? $t('settings.identityProvider.ssoEnabled') : $t('settings.identityProvider.ssoDisabled') }}
            </span>
            <button class="text-[11px] text-blue-600 hover:text-blue-700 flex-shrink-0" @click="ldapModalOpen = true">
              {{ ldapConfig?.url ? $t('settings.identityProvider.ssoEdit') : $t('settings.identityProvider.ssoConfigure') }}
            </button>
          </div>
        </div>
      </template>
      </div>
    </div>

    <!-- ================================================================== -->
    <!-- SCIM Modals (Create + Revoke)                                      -->
    <!-- ================================================================== -->

    <!-- Create Token Modal -->
    <div v-if="showCreateModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="dismissCreateModal">
      <div class="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-sm p-4">
        <h3 class="text-sm font-medium text-gray-900 dark:text-white mb-3">{{ $t('settings.identityProvider.generateTokenTitle') }}</h3>

        <template v-if="!createdToken">
          <div class="mb-3">
            <label class="block text-xs text-gray-600 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.nameLabel') }}</label>
            <input
              v-model="newTokenName"
              type="text"
              :placeholder="$t('settings.identityProvider.namePlaceholder')"
              class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded focus:outline-none focus:border-gray-400"
              @keydown.enter="handleCreateToken"
            />
          </div>
          <div class="flex justify-end gap-2">
            <button class="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700" @click="dismissCreateModal">{{ $t('settings.identityProvider.cancel') }}</button>
            <button
              class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              :disabled="!newTokenName.trim() || creating"
              @click="handleCreateToken"
            >
              {{ creating ? $t('settings.identityProvider.generating') : $t('settings.identityProvider.generate') }}
            </button>
          </div>
        </template>

        <template v-else>
          <div class="rounded border border-amber-200 bg-amber-50 dark:bg-amber-950 p-3 mb-3">
            <div class="flex items-start gap-2">
              <svg class="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              <p class="text-xs font-medium text-amber-800">{{ $t('settings.identityProvider.copyWarning') }}</p>
            </div>
          </div>
          <div class="flex items-center gap-2 mb-3">
            <code class="flex-1 text-[11px] bg-gray-50 dark:bg-gray-900 px-2 py-1.5 rounded border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 font-mono truncate">
              {{ createdToken }}
            </code>
            <button
              class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 flex-shrink-0"
              @click="copyToClipboard(createdToken!, 'token')"
            >
              {{ copied === 'token' ? $t('settings.identityProvider.copied') : $t('settings.identityProvider.copy') }}
            </button>
          </div>
          <div class="flex justify-end">
            <button class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700" @click="dismissCreateModal">{{ $t('settings.identityProvider.done') }}</button>
          </div>
        </template>
      </div>
    </div>

    <!-- Revoke Confirmation Modal -->
    <div v-if="tokenToRevoke" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="tokenToRevoke = null">
      <div class="bg-white dark:bg-gray-900 rounded-lg shadow-lg w-full max-w-sm p-4">
        <h3 class="text-sm font-medium text-gray-900 dark:text-white mb-2">{{ $t('settings.identityProvider.revokeTitle') }}</h3>
        <p class="text-xs text-gray-600 dark:text-gray-400 mb-3">
          {{ $t('settings.identityProvider.revokeWarning', { name: tokenToRevoke.name }) }}
        </p>
        <div class="flex justify-end gap-2">
          <button class="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700" @click="tokenToRevoke = null">{{ $t('settings.identityProvider.cancel') }}</button>
          <button class="px-3 py-1.5 text-xs text-white bg-red-600 rounded hover:bg-red-700" @click="handleRevoke">{{ $t('settings.identityProvider.revoke') }}</button>
        </div>
      </div>
    </div>

    <!-- ================================================================== -->
    <!-- Identity provider modals (SSO + LDAP)                              -->
    <!-- ================================================================== -->

    <!-- ★The modal PUTs the full providers list and hands the saved config
         back, so the page adopts the payload rather than refetching. -->
    <SettingsSsoProviderModal
      v-model="ssoModalOpen"
      :provider="ssoModalProvider"
      :config="ssoConfig"
      @saved="handleSsoModalSaved"
    />

    <!-- LDAP's `saved` carries nothing, so the row refetches to stay current. -->
    <SettingsLdapConfigModal
      v-model="ldapModalOpen"
      @saved="fetchLdapConfig"
    />

  </div>
</template>

<script setup lang="ts">
import { useScimTokens, type ScimToken } from '~/ee/composables/useScimTokens'
import { useLdapSync } from '~/ee/composables/useLdapSync'
import { useSsoProviders, type SsoAuthMode, type SsoConfig } from '~/ee/composables/useSsoProviders'
// Entra profile sync moved into <ProfileSyncSection>, which owns the composable
// itself; this page only supplies each provider's field allowlist.
import { ENTRA_PROFILE_FIELDS, GOOGLE_PROFILE_FIELDS } from '~/composables/useProfileSync'

definePageMeta({
  auth: true,
  permissions: ['manage_identity_providers'],
  layout: 'settings'
})

const { t } = useI18n()
const { hasFeature, license } = useEnterprise()

// ── Collapsible section state (all collapsed by default) ──
const ssoOpen = ref(false)
// No entraOpen: the Entra section is now <ProfileSyncSection>, which owns its
// own collapse state (as does the Google one beside it).
const scimOpen = ref(false)
const ldapOpen = ref(false)

// ── SSO (Single Sign-On) ──
const {
  config: ssoConfig,
  loading: ssoLoading,
  error: ssoError,
  fetchConfig: fetchSsoConfig,
  saveConfig: saveSsoConfig,
} = useSsoProviders()

const hasFetchedSso = ref(false)
const ssoSavedFlash = ref(false)
const ssoAuthMode = ref<SsoAuthMode>('hybrid')

const ssoAuthModes = computed(() => [
  { value: 'local_only' as SsoAuthMode, label: t('settings.identityProvider.ssoModeLocal'), hint: t('settings.identityProvider.ssoModeLocalHint') },
  { value: 'hybrid' as SsoAuthMode, label: t('settings.identityProvider.ssoModeHybrid'), hint: t('settings.identityProvider.ssoModeHybridHint') },
  { value: 'sso_only' as SsoAuthMode, label: t('settings.identityProvider.ssoModeSso'), hint: t('settings.identityProvider.ssoModeSsoHint') },
])

// Fixed, always-visible provider set. Each row maps to a canonical entry:
// Keycloak / Generic OIDC / Entra ID → an oidc_providers entry keyed by
// `canonical`; Google → the dedicated config.google block. Rows render whether
// or not the provider exists in the saved config yet.
const SSO_FIXED_ROWS = [
  { key: 'keycloak', canonical: 'keycloak', label: 'Keycloak', type: 'OIDC', icon: 'keycloak', isGoogle: false },
  { key: 'oidc', canonical: 'oidc', label: 'Generic OIDC', type: 'OIDC', icon: 'oidc', isGoogle: false },
  { key: '__google__', canonical: 'google', label: 'Google', type: 'OAuth', icon: 'google', isGoogle: true },
  { key: 'entra', canonical: 'entra', label: 'Entra ID', type: 'OIDC', icon: 'entra', isGoogle: false },
]

// Rows for the provider list: always the 4 fixed rows, hydrated from whatever
// exists in the saved config. `configured` gates the "Not configured" badge
// (google = client_id + secret; oidc = issuer + client_id).
const ssoRows = computed(() => {
  const c = ssoConfig.value
  return SSO_FIXED_ROWS.map((r) => {
    if (r.isGoogle) {
      const g = c?.google
      const configured = !!(g && g.client_id && g.client_secret_set)
      return { ...r, name: 'google', enabled: !!g?.enabled, exists: !!g, configured, issuer: '' }
    }
    const p = c?.providers?.find((x) => x.name === r.canonical)
    const configured = !!(p && p.issuer && p.client_id)
    return {
      ...r,
      name: r.canonical,
      label: (p && p.label) || r.label,
      // ★The SAVED mark wins over the row's default. Spreading `...r` alone
      // kept the canonical icon forever, so the Logo picker appeared to do
      // nothing: it wrote `icon` to the config and this row never read it back.
      icon: (p && p.icon) || r.icon,
      enabled: !!(p && p.enabled),
      exists: !!p,
      configured,
      issuer: (p && p.issuer) || '',
    }
  })
})

// Build a minimal enabled OIDC entry for a fixed row that has no saved config
// yet (blank issuer/client_id allowed — server no longer requires them to
// enable; surfaces as "not configured" until the admin fills it in).
const minimalOidcProvider = (row: any) => ({
  name: row.canonical,
  enabled: true,
  issuer: '',
  client_id: '',
  scopes: ['openid', 'email', 'profile'],
  label: row.label,
  icon: row.icon,
  pkce: true,
  discovery: true,
  uid_claim: 'sub',
  sync_groups: false,
  group_claim: 'groups',
  resolve_group_names: false,
  auto_provision: false,
})

// Edit dialog state. The row is handed to the modal as-is; the modal owns the
// form, the payload and the save.
const ssoModalOpen = ref(false)
const ssoModalProvider = ref<any | null>(null)

const applySsoConfig = () => {
  if (ssoConfig.value) ssoAuthMode.value = ssoConfig.value.auth_mode
}

const flashSsoSaved = () => {
  ssoSavedFlash.value = true
  setTimeout(() => { ssoSavedFlash.value = false }, 3000)
}

// Serialize existing providers for a structural save (toggle/remove) without
// touching secrets — omitting client_secret keeps the stored one.
const serializeProviders = (list: any[]) => list.map((p) => {
  const { client_secret_set, ...rest } = p
  return { ...rest }
})

const handleSetAuthMode = async (mode: SsoAuthMode) => {
  ssoAuthMode.value = mode
  const saved = await saveSsoConfig({ auth_mode: mode })
  if (saved) { applySsoConfig(); flashSsoSaved() }
}

const handleToggleProvider = async (row: any) => {
  if (!ssoConfig.value) return
  if (row.isGoogle) {
    const g = ssoConfig.value.google
    const saved = await saveSsoConfig({ google: { enabled: !g.enabled, client_id: g.client_id } })
    if (saved) flashSsoSaved()
    return
  }
  const existing = ssoConfig.value.providers
  const found = existing.find((p) => p.name === row.canonical)
  let providers: any[]
  if (found) {
    // Toggle the saved entry (secrets preserved — serialize drops the set-flag).
    providers = serializeProviders(existing).map((p) =>
      p.name === row.canonical ? { ...p, enabled: !p.enabled } : p
    )
  } else {
    // No saved entry yet — toggling ON creates a minimal enabled one.
    providers = serializeProviders(existing)
    providers.push(minimalOidcProvider(row))
  }
  const saved = await saveSsoConfig({ providers })
  if (saved) flashSsoSaved()
}

const handleEditProvider = (row: any) => {
  if (!ssoConfig.value) return
  ssoModalProvider.value = row
  ssoModalOpen.value = true
}

// ★The modal saved the whole config and returned the server's copy; adopting it
// keeps the row list and the modal from drifting without a second GET.
const handleSsoModalSaved = (saved: SsoConfig) => {
  ssoConfig.value = saved
  applySsoConfig()
  flashSsoSaved()
}

const initSso = async () => {
  if (hasFetchedSso.value) return
  hasFetchedSso.value = true
  await fetchSsoConfig()
  applySsoConfig()
}

// ── Profile sync sections (Entra + Google) ──
// Fields shown as default rows when nothing is configured yet.
const ENTRA_DEFAULT_VISIBLE = ['jobTitle', 'department', 'companyName', 'officeLocation']
const GOOGLE_DEFAULT_VISIBLE = ['displayName', 'jobTitle', 'department', 'organization']

// ── SCIM ──
const { tokens, loading: scimLoading, error: scimError, fetchTokens, createToken, revokeToken } = useScimTokens()

const showCreateModal = ref(false)
const newTokenName = ref('SCIM Token')
const creating = ref(false)
const createdToken = ref<string | null>(null)
const tokenToRevoke = ref<ScimToken | null>(null)
const copied = ref<string | null>(null)
const hasFetchedScim = ref(false)

const scimBaseUrl = computed(() => {
  if (process.client) {
    return `${window.location.origin}/scim/v2`
  }
  return '/scim/v2'
})

const dismissCreateModal = () => {
  showCreateModal.value = false
  createdToken.value = null
  newTokenName.value = 'SCIM Token'
}

const handleCreateToken = async () => {
  if (!newTokenName.value.trim() || creating.value) return
  creating.value = true
  const result = await createToken(newTokenName.value.trim())
  creating.value = false
  if (result) {
    createdToken.value = result.token
  }
}

const confirmRevoke = (token: ScimToken) => {
  tokenToRevoke.value = token
}

const handleRevoke = async () => {
  if (!tokenToRevoke.value) return
  await revokeToken(tokenToRevoke.value.id)
  tokenToRevoke.value = null
  createdToken.value = null
}

// ── LDAP ──
// ★Only the READ side lives here now. The form, its payload, Save & Test and
// the sync/preview actions all moved into <SettingsLdapConfigModal>, which
// opens its own composable instance; the page keeps just enough to draw the
// row and to refresh it when the modal reports a save.
const {
  config: ldapConfig,
  fetchConfig: fetchLdapConfig,
} = useLdapSync()

const hasFetchedLdap = ref(false)
const ldapModalOpen = ref(false)

// Row subtitle: server URL, then base DN — the two facts that say which
// directory this is pointed at.
const ldapSummary = computed(() => {
  const c = ldapConfig.value
  if (!c) return ''
  return [c.url, c.base_dn].filter(Boolean).join(' · ')
})

// ── Shared ──
const copyToClipboard = async (text: string, key: string = 'url') => {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    setTimeout(() => { copied.value = null }, 2000)
  } catch {
    // Fallback
  }
}

const { relativeTime: formatRelativeTime } = useRelativeTime()

// ── Init ──
// SSO is not enterprise-gated — load immediately. The two profile-sync
// sections fetch their own config from inside <ProfileSyncSection>, so there
// is no initEntra() to call here any more.
onMounted(() => { initSso() })

watch(
  () => license.value,
  (newLicense) => {
    if (newLicense && hasFeature('scim') && !hasFetchedScim.value) {
      hasFetchedScim.value = true
      fetchTokens()
    }
    if (newLicense && hasFeature('ldap') && !hasFetchedLdap.value) {
      hasFetchedLdap.value = true
      fetchLdapConfig()
    }
  },
  { immediate: true }
)
</script>
