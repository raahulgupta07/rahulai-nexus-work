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
            <!-- Logo chip -->
            <span class="inline-flex items-center justify-center w-6 h-6 rounded text-[10px] font-bold text-white flex-shrink-0" :class="ssoChipClass(row)">
              {{ (row.label || row.name || '?').charAt(0).toUpperCase() }}
            </span>
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
            <!-- Configure / Edit (opens the existing inline edit form) -->
            <button class="text-[11px] text-blue-600 hover:text-blue-700 flex-shrink-0" @click="handleEditProvider(row)">{{ row.configured ? $t('settings.identityProvider.ssoEdit') : 'Configure' }}</button>
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

        <!-- Inline edit form -->
        <div v-if="ssoEditingKey" class="rounded border border-gray-200 dark:border-gray-700 p-4 mb-3">
          <div class="flex items-center justify-between mb-3">
            <span class="text-xs font-medium text-gray-700 dark:text-gray-300">
              {{ ssoEditingIsNew ? $t('settings.identityProvider.ssoAddProvider') : $t('settings.identityProvider.ssoEdit') }}
              <span class="text-gray-400 dark:text-gray-500">· {{ ssoForm.label }}</span>
            </span>
            <button class="text-gray-300 hover:text-gray-500" :title="$t('settings.identityProvider.ssoCancel')" @click="closeSsoEdit">
              <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
            </button>
          </div>

          <div class="space-y-3">
            <!-- Enable -->
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="ssoForm.enabled" class="rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoEnableProvider') }}</span>
            </label>

            <!-- ★Admission, which is a different question from authentication.
                 Signing in proves who somebody is; this decides whether that is
                 enough to get an account. Off by default. -->
            <label class="flex items-start gap-2 cursor-pointer">
              <input type="checkbox" v-model="ssoForm.auto_provision" class="mt-0.5 rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">
                {{ $t('settings.identityProvider.ssoAutoProvision') }}
                <span class="block text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ssoAutoProvisionHint') }}</span>
              </span>
            </label>

            <!-- Label (hidden for google) -->
            <div v-if="!ssoEditingIsGoogle">
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoLabel') }}</label>
              <input v-model="ssoForm.label" type="text" placeholder="Keycloak" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>

            <!-- Issuer (OIDC only — hidden for google/OAuth) -->
            <div v-if="!ssoEditingIsGoogle">
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoIssuer') }}</label>
              <input v-model="ssoForm.issuer" type="text" placeholder="https://id.corp.com/realms/main" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200 font-mono" />
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoClientId') }}</label>
                <input v-model="ssoForm.client_id" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoClientSecret') }}</label>
                <input v-model="ssoForm.client_secret" type="password" autocomplete="new-password" :placeholder="ssoEditSecretSet ? $t('settings.identityProvider.ssoSecretSaved') : ''" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
            </div>

            <!-- Scopes (OIDC only) -->
            <div v-if="!ssoEditingIsGoogle">
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoScopes') }}</label>
              <input v-model="ssoForm.scopesText" type="text" placeholder="openid, email, profile" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>

            <!-- Checkboxes (OIDC only) -->
            <div v-if="!ssoEditingIsGoogle" class="flex items-center flex-wrap gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="ssoForm.pkce" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoPkce') }}</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="ssoForm.discovery" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoDiscovery') }}</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="ssoForm.sync_groups" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoSyncGroups') }}</span>
              </label>
            </div>

            <!-- Group claim (when sync groups on, OIDC only) -->
            <div v-if="!ssoEditingIsGoogle && ssoForm.sync_groups" class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ssoGroupClaim') }}</label>
                <input v-model="ssoForm.group_claim" type="text" placeholder="groups" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div class="flex items-end">
                <label class="flex items-center gap-2 cursor-pointer pb-1.5">
                  <input type="checkbox" v-model="ssoForm.resolve_group_names" class="rounded border-gray-300 dark:border-gray-600" />
                  <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ssoResolveGroupNames') }}</span>
                </label>
              </div>
            </div>
          </div>

          <!-- Save / cancel -->
          <div class="flex items-center gap-2 border-t border-gray-100 dark:border-gray-800 pt-3 mt-4">
            <button
              class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              :disabled="ssoSaving"
              @click="handleSaveProvider"
            >
              {{ ssoSaving ? $t('settings.identityProvider.ssoSaving') : $t('settings.identityProvider.ssoSave') }}
            </button>
            <button
              class="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300"
              @click="closeSsoEdit"
            >
              {{ $t('settings.identityProvider.ssoCancel') }}
            </button>
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
    <div class="border border-gray-200 dark:border-gray-700 rounded-lg">
      <button
        class="w-full flex items-center justify-between px-4 py-3 text-left"
        @click="entraOpen = !entraOpen"
      >
        <div>
          <h2 class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.identityProvider.entraTitle') }}</h2>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('settings.identityProvider.entraSubtitle') }}</p>
        </div>
        <svg class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0" :class="entraOpen ? 'rotate-180' : ''" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      <div v-show="entraOpen" class="px-4 pb-4 border-t border-gray-100 dark:border-gray-800 pt-4">
        <!-- Enable toggle -->
        <div class="flex items-start justify-between gap-4 mb-4">
          <div class="flex-1">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.entraEnable') }}</div>
            <p class="text-[11px] text-gray-400 dark:text-gray-400 mt-0.5">{{ $t('settings.identityProvider.entraEnableHint') }}</p>
          </div>
          <button
            type="button"
            role="switch"
            :aria-checked="entraEnabled"
            class="relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition-colors focus:outline-none"
            :class="entraEnabled ? 'bg-blue-600' : 'bg-gray-200 dark:bg-gray-700'"
            @click="entraEnabled = !entraEnabled"
          >
            <span
              class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform mt-0.5"
              :class="entraEnabled ? 'translate-x-4' : 'translate-x-0.5'"
            ></span>
          </button>
        </div>

        <!-- Fields to include in context -->
        <div v-show="entraEnabled">
          <div class="flex items-center justify-between mb-2">
            <label class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.entraFieldsLabel') }}</label>
            <span v-if="previewLoading" class="text-[11px] text-gray-400">
              <span class="inline-block w-3 h-3 border-2 border-gray-200 dark:border-gray-700 border-t-gray-500 rounded-full animate-spin align-middle"></span>
            </span>
          </div>

          <!-- Preview connection hint -->
          <p v-if="preview && !preview.connected" class="text-[11px] text-amber-600 dark:text-amber-500 mb-2">
            {{ preview.error === 'no_entra_login'
              ? $t('settings.identityProvider.entraNoLogin')
              : preview.error === 'reauth_required'
                ? $t('settings.identityProvider.entraReauth')
                : $t('settings.identityProvider.entraPreviewError') }}
          </p>

          <!-- Field rows: checkbox (in context) | label | sample value | remove -->
          <div class="border border-gray-200 dark:border-gray-700 rounded overflow-hidden">
            <div
              v-for="(field, idx) in shownFields"
              :key="field"
              class="flex items-center gap-2 px-3 py-2 text-xs"
              :class="{ 'border-t border-gray-100 dark:border-gray-800': idx > 0 }"
            >
              <input
                :id="`entra-field-${field}`"
                type="checkbox"
                :checked="selected.has(field)"
                class="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                @change="toggleField(field)"
              />
              <label :for="`entra-field-${field}`" class="w-40 flex-shrink-0 text-gray-700 dark:text-gray-300 cursor-pointer">
                {{ fieldLabel(field) }}
              </label>
              <span class="flex-1 truncate font-mono text-[11px]" :class="sampleValue(field) ? 'text-gray-500 dark:text-gray-400' : 'text-gray-300 dark:text-gray-600 italic'">
                {{ sampleValue(field) || $t('settings.identityProvider.entraNotSet') }}
              </span>
              <button
                class="text-gray-300 hover:text-red-500 flex-shrink-0"
                :title="$t('settings.identityProvider.entraRemove')"
                @click="removeField(field)"
              >
                <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
              </button>
            </div>
            <div v-if="!shownFields.length" class="px-3 py-4 text-center text-[11px] text-gray-400">
              {{ $t('settings.identityProvider.entraNoFields') }}
            </div>
          </div>

          <!-- Add attribute -->
          <div v-if="addableFields.length" class="flex items-center gap-2 mt-2">
            <select
              v-model="fieldToAdd"
              class="text-xs border border-gray-200 dark:border-gray-700 dark:bg-gray-900 rounded px-2 py-1.5 focus:outline-none focus:border-gray-400"
            >
              <option value="">{{ $t('settings.identityProvider.entraAddPlaceholder') }}</option>
              <option v-for="f in addableFields" :key="f" :value="f">{{ fieldLabel(f) }}</option>
            </select>
            <button
              class="px-2 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 disabled:opacity-40"
              :disabled="!fieldToAdd"
              @click="addField"
            >
              {{ $t('settings.identityProvider.entraAdd') }}
            </button>
          </div>
        </div>

        <!-- Save -->
        <div class="flex items-center gap-3 mt-4">
          <button
            class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
            :disabled="entraSaving"
            @click="handleEntraSave"
          >
            {{ entraSaving ? $t('settings.identityProvider.entraSaving') : $t('common.save') }}
          </button>
          <span v-if="entraSavedFlash" class="text-[11px] text-green-600">{{ $t('settings.identityProvider.entraSaved') }}</span>
          <span v-if="entraError" class="text-[11px] text-red-500">{{ entraError }}</span>
        </div>
      </div>
    </div>

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
        <!-- ── Editable LDAP config form (per-org, saved to DB) ────────── -->
        <div class="rounded border border-gray-200 dark:border-gray-700 p-4 mb-4">
          <!-- Enable toggle -->
          <label class="flex items-center gap-2 mb-4 cursor-pointer">
            <input type="checkbox" v-model="ldapForm.enabled" class="rounded border-gray-300 dark:border-gray-600" />
            <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.ldapEnable') }}</span>
          </label>

          <!-- Connection -->
          <p class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ $t('settings.identityProvider.ldapSectionConnection') }}</p>
          <div class="space-y-3 mb-4">
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapUrl') }}</label>
              <input v-model="ldapForm.url" type="text" placeholder="ldaps://ad.corp.com:636" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>
            <div class="flex items-center gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="ldapForm.use_ssl" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapUseSsl') }}</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" v-model="ldapForm.start_tls" class="rounded border-gray-300 dark:border-gray-600" />
                <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapStartTls') }}</span>
              </label>
              <div class="flex items-center gap-1">
                <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapTimeout') }}</span>
                <input v-model.number="ldapForm.connection_timeout" type="number" min="1" class="w-16 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
            </div>
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapBindDn') }}</label>
              <input v-model="ldapForm.bind_dn" type="text" placeholder="cn=svc-bow,ou=svc,dc=corp,dc=com" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapBindPassword') }}</label>
              <input v-model="ldapForm.bind_password" type="password" autocomplete="new-password" :placeholder="ldapBindPasswordSet ? $t('settings.identityProvider.ldapPasswordSaved') : ''" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              <p class="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">{{ $t('settings.identityProvider.ldapPasswordHint') }}</p>
            </div>
          </div>

          <!-- Directory tree -->
          <p class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ $t('settings.identityProvider.ldapSectionTree') }}</p>
          <div class="space-y-3 mb-4">
            <div>
              <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapBaseDn') }}</label>
              <input v-model="ldapForm.base_dn" type="text" placeholder="dc=corp,dc=com" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapUserSearchBase') }}</label>
                <input v-model="ldapForm.user_search_base" type="text" :placeholder="$t('settings.identityProvider.ldapDefaultsBaseDn')" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapUserFilter') }}</label>
                <input v-model="ldapForm.user_search_filter" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapEmailAttr') }}</label>
                <input v-model="ldapForm.user_email_attribute" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapNameAttr') }}</label>
                <input v-model="ldapForm.user_name_attribute" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapGroupSearchBase') }}</label>
                <input v-model="ldapForm.group_search_base" type="text" :placeholder="$t('settings.identityProvider.ldapDefaultsBaseDn')" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapGroupFilter') }}</label>
                <input v-model="ldapForm.group_search_filter" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapGroupNameAttr') }}</label>
                <input v-model="ldapForm.group_name_attribute" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapMemberAttr') }}</label>
                <input v-model="ldapForm.group_member_attribute" type="text" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              </div>
              <div>
                <label class="block text-[11px] text-gray-500 dark:text-gray-400 mb-1">{{ $t('settings.identityProvider.ldapMemberFormat') }}</label>
                <select v-model="ldapForm.group_member_format" class="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200">
                  <option value="dn">dn</option>
                  <option value="uid">uid</option>
                </select>
              </div>
            </div>
          </div>

          <!-- Sync -->
          <p class="text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ $t('settings.identityProvider.ldapSectionSync') }}</p>
          <div class="flex items-center flex-wrap gap-4 mb-4">
            <div class="flex items-center gap-1">
              <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapInterval') }}</span>
              <input v-model.number="ldapForm.sync_interval_minutes" type="number" min="1" class="w-16 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ldapMinutes') }}</span>
            </div>
            <div class="flex items-center gap-1">
              <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapPageSize') }}</span>
              <input v-model.number="ldapForm.page_size" type="number" min="1" class="w-20 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </div>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="ldapForm.auto_provision_users" class="rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapAutoProvision') }}</span>
            </label>
          </div>

          <!-- Save / Test -->
          <div class="flex items-center gap-2 border-t border-gray-100 dark:border-gray-800 pt-3">
            <button
              class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              :disabled="ldapSaving"
              @click="handleSaveLdap"
            >
              {{ ldapSaving ? $t('settings.identityProvider.saving') : $t('settings.identityProvider.save') }}
            </button>
            <button
              class="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 disabled:opacity-50"
              :disabled="ldapLoading || ldapSaving"
              @click="handleTestConnection"
            >
              {{ ldapLoading ? $t('settings.identityProvider.testing') : $t('settings.identityProvider.saveAndTest') }}
            </button>
            <span v-if="ldapSavedFlash" class="text-[11px] text-green-600">{{ $t('settings.identityProvider.saved') }}</span>
            <span v-if="ldapConfig && !ldapConfig.source_db" class="text-[11px] text-gray-400 dark:text-gray-500 ms-auto">{{ $t('settings.identityProvider.ldapFromFile') }}</span>
          </div>
        </div>

        <!-- ── Status + sync actions (only when LDAP is on) ───────────── -->
        <template v-if="ldapForm.enabled || ldapStatus?.ldap_configured">
          <!-- Connection status -->
          <div class="rounded border border-gray-200 dark:border-gray-700 p-3 mb-4">
            <div class="flex items-center justify-between">
              <div>
                <div class="flex items-center gap-2">
                  <div
                    class="w-2 h-2 rounded-full"
                    :class="ldapTestResult?.connected ? 'bg-green-500' : (ldapTestResult ? 'bg-red-500' : 'bg-gray-300 dark:bg-gray-600')"
                  ></div>
                  <span class="text-xs font-medium text-gray-700 dark:text-gray-300">
                    {{ ldapTestResult?.connected ? $t('settings.identityProvider.statusConnected') : (ldapTestResult ? $t('settings.identityProvider.statusFailed') : $t('settings.identityProvider.statusNotTested')) }}
                  </span>
                </div>
                <p v-if="ldapTestResult?.connected" class="text-[11px] text-gray-400 dark:text-gray-400 mt-0.5 ms-4">
                  {{ ldapTestResult.server }}
                  <template v-if="ldapTestResult.vendor"> · {{ ldapTestResult.vendor }}</template>
                  <template v-if="ldapTestResult.user_count !== null"> · {{ ldapTestResult.user_count }} users</template>
                  <template v-if="ldapTestResult.group_count !== null"> · {{ ldapTestResult.group_count }} groups</template>
                </p>
                <p v-if="ldapTestResult && !ldapTestResult.connected" class="text-[11px] text-red-400 mt-0.5 ms-4">
                  {{ ldapTestResult.error }}
                </p>
              </div>
              <button
                class="px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300"
                :disabled="ldapLoading"
                @click="handleTestConnection"
              >
                {{ ldapLoading ? $t('settings.identityProvider.testing') : $t('settings.identityProvider.testConnection') }}
              </button>
            </div>
          </div>

          <!-- Last sync info -->
          <div v-if="ldapStatus?.last_sync" class="rounded border border-gray-200 dark:border-gray-700 p-3 mb-4">
            <div class="flex items-center justify-between">
              <div>
                <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.lastSync') }}</span>
                <p class="text-[11px] text-gray-400 dark:text-gray-400 mt-0.5">
                  {{ ldapStatus.last_sync.timestamp ? formatRelativeTime(ldapStatus.last_sync.timestamp) : $t('settings.identityProvider.unknown') }}
                  {{ $t('settings.identityProvider.lastSyncDetail', {
                    gCreated: ldapStatus.last_sync.groups_created,
                    gUpdated: ldapStatus.last_sync.groups_updated,
                    gRemoved: ldapStatus.last_sync.groups_removed,
                    mAdded: ldapStatus.last_sync.memberships_added,
                    mRemoved: ldapStatus.last_sync.memberships_removed,
                  }) }}
                </p>
                <p v-if="ldapStatus.last_sync.errors.length" class="text-[11px] text-red-400 mt-0.5">
                  {{ $t('settings.identityProvider.errorCount', { n: ldapStatus.last_sync.errors.length, first: ldapStatus.last_sync.errors[0] }) }}
                </p>
              </div>
            </div>
          </div>

          <!-- Sync actions -->
          <div class="flex items-center gap-2">
            <button
              class="px-2 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
              :disabled="ldapLoading"
              @click="handleSync"
            >
              {{ ldapLoading ? $t('settings.identityProvider.syncing') : $t('settings.identityProvider.syncNow') }}
            </button>
            <button
              class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 disabled:opacity-50"
              :disabled="ldapLoading"
              @click="handlePreview"
            >
              {{ $t('settings.identityProvider.previewChanges') }}
            </button>
          </div>

          <!-- Sync result flash -->
          <div v-if="lastSyncResult" class="mt-3 rounded border border-green-200 bg-green-50 dark:bg-green-950 p-3">
            <p class="text-xs text-green-700">
              {{ $t('settings.identityProvider.syncCompletedSummary', {
                gCreated: lastSyncResult.groups_created,
                gUpdated: lastSyncResult.groups_updated,
                gRemoved: lastSyncResult.groups_removed,
                mAdded: lastSyncResult.memberships_added,
                mRemoved: lastSyncResult.memberships_removed,
              }) }}
              <template v-if="lastSyncResult.users_not_found">
                {{ $t('settings.identityProvider.ldapUsersNotFound', { n: lastSyncResult.users_not_found }) }}
              </template>
            </p>
          </div>

          <!-- Preview results -->
          <div v-if="ldapPreview" class="mt-3">
            <div class="rounded border border-gray-200 dark:border-gray-700 overflow-hidden">
              <div class="bg-gray-50 dark:bg-gray-900 px-3 py-2 border-b border-gray-200 dark:border-gray-700">
                <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.preview') }} </span>
                <span class="text-xs text-gray-500 dark:text-gray-400">
                  {{ $t('settings.identityProvider.previewSummary', {
                    create: ldapPreview.groups_to_create,
                    update: ldapPreview.groups_to_update,
                    remove: ldapPreview.groups_to_remove,
                    changes: ldapPreview.total_membership_changes,
                  }) }}
                </span>
              </div>
              <div v-if="ldapPreview.groups.length" class="max-h-64 overflow-y-auto">
                <div
                  v-for="(group, idx) in ldapPreview.groups"
                  :key="group.dn"
                  class="flex items-center px-3 py-2 text-xs"
                  :class="{ 'border-t border-gray-100 dark:border-gray-800': idx > 0 }"
                >
                  <span class="flex-1 text-gray-700 dark:text-gray-300 truncate" :title="group.dn">{{ group.name }}</span>
                  <span class="w-20 text-gray-400 dark:text-gray-400 text-[11px]">{{ $t('settings.identityProvider.memberCount', { n: group.member_count }) }}</span>
                  <span class="w-24 text-[11px]" :class="group.exists_in_app ? 'text-gray-400 dark:text-gray-400' : 'text-blue-500'">
                    {{ group.exists_in_app ? $t('settings.identityProvider.groupExists') : $t('settings.identityProvider.groupNew') }}
                  </span>
                  <span v-if="group.members_to_add" class="text-[11px] text-green-600 me-2">+{{ group.members_to_add }}</span>
                  <span v-if="group.members_to_remove" class="text-[11px] text-red-500">-{{ group.members_to_remove }}</span>
                </div>
              </div>
              <div v-else class="py-4 text-center text-xs text-gray-400 dark:text-gray-400">
                {{ $t('settings.identityProvider.noLdapGroups') }}
              </div>
            </div>
          </div>

          <!-- LDAP Error -->
          <div v-if="ldapError" class="mt-3 text-xs text-red-500">
            {{ ldapError }}
          </div>
        </template>
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

  </div>
</template>

<script setup lang="ts">
import { useScimTokens, type ScimToken } from '~/ee/composables/useScimTokens'
import { useLdapSync, type SyncResult as LDAPSyncResult } from '~/ee/composables/useLdapSync'
import { useSsoProviders, type SsoAuthMode } from '~/ee/composables/useSsoProviders'
import { useEntraProfileSync, ENTRA_PROFILE_FIELDS } from '~/composables/useEntraProfileSync'

definePageMeta({
  auth: true,
  permissions: ['manage_identity_providers'],
  layout: 'settings'
})

const { t } = useI18n()
const { hasFeature, license } = useEnterprise()

// ── Collapsible section state (all collapsed by default) ──
const ssoOpen = ref(false)
const entraOpen = ref(false)
const scimOpen = ref(false)
const ldapOpen = ref(false)

// ── SSO (Single Sign-On) ──
const {
  config: ssoConfig,
  saving: ssoSaving,
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

const ssoChipClass = (row: any) => {
  if (row.isGoogle) return 'bg-red-500'
  const byIcon: Record<string, string> = { keycloak: 'bg-indigo-500', entra: 'bg-sky-600', oidc: 'bg-slate-500' }
  return byIcon[row.icon] || 'bg-slate-500'
}

// Edit-form state. ssoEditingKey null = form closed. ssoEditingIsGoogle routes
// the save through config.google (OAuth, no issuer/scopes).
const ssoEditingKey = ref<string | null>(null)
const ssoEditingIsGoogle = ref(false)
const ssoEditingIsNew = ref(false)
const ssoEditSecretSet = ref(false)

const ssoForm = reactive({
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
  ssoForm.client_secret = ''
  if (row.isGoogle) {
    const g = ssoConfig.value.google
    ssoEditingIsGoogle.value = true
    ssoEditingIsNew.value = false
    ssoEditingKey.value = '__google__'
    ssoForm.name = 'google'
    ssoForm.label = 'Google'
    ssoForm.enabled = g.enabled
    ssoForm.client_id = g.client_id || ''
    ssoForm.auto_provision = !!g.auto_provision
    ssoEditSecretSet.value = g.client_secret_set
    return
  }
  ssoEditingIsGoogle.value = false
  ssoEditingKey.value = row.canonical
  const p = ssoConfig.value.providers.find((x) => x.name === row.canonical)
  if (p) {
    // Configured (or partially configured) — edit the saved entry in place.
    ssoEditingIsNew.value = false
    ssoForm.name = p.name
    ssoForm.label = p.label || row.label
    ssoForm.enabled = p.enabled
    ssoForm.issuer = p.issuer
    ssoForm.client_id = p.client_id
    ssoForm.scopesText = (p.scopes || []).join(', ')
    ssoForm.icon = p.icon || row.icon
    ssoForm.pkce = p.pkce
    ssoForm.discovery = p.discovery
    ssoForm.uid_claim = p.uid_claim
    ssoForm.sync_groups = p.sync_groups
    ssoForm.group_claim = p.group_claim
    ssoForm.resolve_group_names = p.resolve_group_names
    ssoForm.auto_provision = !!p.auto_provision
    ssoEditSecretSet.value = p.client_secret_set
  } else {
    // Not configured yet — preset a minimal form under the canonical name.
    ssoEditingIsNew.value = true
    ssoForm.name = row.canonical
    ssoForm.label = row.label
    ssoForm.enabled = true
    ssoForm.issuer = ''
    ssoForm.client_id = ''
    ssoForm.scopesText = 'openid, email, profile'
    ssoForm.icon = row.icon
    ssoForm.pkce = true
    ssoForm.discovery = true
    ssoForm.uid_claim = 'sub'
    ssoForm.sync_groups = false
    ssoForm.group_claim = 'groups'
    ssoForm.resolve_group_names = false
    ssoForm.auto_provision = false
    ssoEditSecretSet.value = false
  }
}

const closeSsoEdit = () => {
  ssoEditingKey.value = null
  ssoEditingIsGoogle.value = false
  ssoEditingIsNew.value = false
  ssoForm.client_secret = ''
}

const handleSaveProvider = async () => {
  if (!ssoConfig.value) return
  if (ssoEditingIsGoogle.value) {
    const g: any = { enabled: ssoForm.enabled, client_id: ssoForm.client_id, auto_provision: ssoForm.auto_provision }
    if (ssoForm.client_secret) g.client_secret = ssoForm.client_secret
    const saved = await saveSsoConfig({ google: g })
    if (saved) { closeSsoEdit(); flashSsoSaved() }
    return
  }
  const entry: any = {
    name: ssoForm.name,
    enabled: ssoForm.enabled,
    issuer: ssoForm.issuer,
    client_id: ssoForm.client_id,
    scopes: ssoForm.scopesText.split(',').map((s) => s.trim()).filter(Boolean),
    label: ssoForm.label,
    icon: ssoForm.icon,
    pkce: ssoForm.pkce,
    discovery: ssoForm.discovery,
    uid_claim: ssoForm.uid_claim,
    sync_groups: ssoForm.sync_groups,
    group_claim: ssoForm.group_claim,
    resolve_group_names: ssoForm.resolve_group_names,
    auto_provision: ssoForm.auto_provision,
  }
  if (ssoForm.client_secret) entry.client_secret = ssoForm.client_secret
  const existing = ssoConfig.value.providers
  const providers = serializeProviders(existing).map((p) => (p.name === entry.name ? entry : p))
  if (!existing.find((p) => p.name === entry.name)) providers.push(entry)
  const saved = await saveSsoConfig({ providers })
  if (saved) { closeSsoEdit(); flashSsoSaved() }
}

const initSso = async () => {
  if (hasFetchedSso.value) return
  hasFetchedSso.value = true
  await fetchSsoConfig()
  applySsoConfig()
}

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
const {
  status: ldapStatus,
  preview: ldapPreview,
  testResult: ldapTestResult,
  config: ldapConfig,
  saving: ldapSaving,
  loading: ldapLoading,
  error: ldapError,
  fetchConfig: fetchLdapConfig,
  saveConfig: saveLdapConfig,
  fetchStatus: fetchLdapStatus,
  triggerSync,
  fetchPreview,
  testConnection,
} = useLdapSync()

const lastSyncResult = ref<LDAPSyncResult | null>(null)
const hasFetchedLdap = ref(false)
const ldapSavedFlash = ref(false)

// Editable form model. bindPassword is a write-only field: blank keeps the saved
// (encrypted) value; typing a value re-sets it. bindPasswordSet reflects whether
// a password is already stored, so the field can show a "saved" placeholder.
const ldapForm = reactive({
  enabled: false,
  url: '',
  bind_dn: '',
  bind_password: '',
  use_ssl: true,
  start_tls: false,
  base_dn: '',
  user_search_base: '',
  user_search_filter: '(objectClass=person)',
  user_email_attribute: 'mail',
  user_name_attribute: 'cn',
  group_search_base: '',
  group_search_filter: '(objectClass=group)',
  group_name_attribute: 'cn',
  group_member_attribute: 'member',
  group_member_format: 'dn',
  sync_interval_minutes: 60,
  auto_provision_users: false,
  connection_timeout: 10,
  page_size: 500,
})
const ldapBindPasswordSet = ref(false)

const applyLdapConfigToForm = () => {
  const c = ldapConfig.value
  if (!c) return
  ldapForm.enabled = c.enabled
  ldapForm.url = c.url || ''
  ldapForm.bind_dn = c.bind_dn || ''
  ldapForm.bind_password = ''
  ldapForm.use_ssl = c.use_ssl
  ldapForm.start_tls = c.start_tls
  ldapForm.base_dn = c.base_dn || ''
  ldapForm.user_search_base = c.user_search_base || ''
  ldapForm.user_search_filter = c.user_search_filter
  ldapForm.user_email_attribute = c.user_email_attribute
  ldapForm.user_name_attribute = c.user_name_attribute
  ldapForm.group_search_base = c.group_search_base || ''
  ldapForm.group_search_filter = c.group_search_filter
  ldapForm.group_name_attribute = c.group_name_attribute
  ldapForm.group_member_attribute = c.group_member_attribute
  ldapForm.group_member_format = c.group_member_format
  ldapForm.sync_interval_minutes = c.sync_interval_minutes
  ldapForm.auto_provision_users = c.auto_provision_users
  ldapForm.connection_timeout = c.connection_timeout
  ldapForm.page_size = c.page_size
  ldapBindPasswordSet.value = c.bind_password_set
}

const buildLdapPayload = () => {
  const p: any = { ...ldapForm }
  // Only send bind_password when the user typed a new one.
  if (!ldapForm.bind_password) delete p.bind_password
  return p
}

const handleSaveLdap = async () => {
  const saved = await saveLdapConfig(buildLdapPayload())
  if (saved) {
    applyLdapConfigToForm()
    ldapSavedFlash.value = true
    setTimeout(() => { ldapSavedFlash.value = false }, 4000)
  }
}

const handleTestConnection = async () => {
  // Test runs against the saved config, so persist the current form first.
  await handleSaveLdap()
  await testConnection()
}

const handleSync = async () => {
  lastSyncResult.value = null
  ldapPreview.value = null
  const result = await triggerSync()
  if (result) {
    lastSyncResult.value = result
    setTimeout(() => { lastSyncResult.value = null }, 15000)
  }
}

const handlePreview = async () => {
  lastSyncResult.value = null
  await fetchPreview()
}

// ── Entra ID profile sync ──
const {
  config: entraConfig,
  preview,
  previewLoading,
  saving: entraSaving,
  error: entraError,
  fetchConfig: fetchEntraConfig,
  save: saveEntra,
  fetchPreview: fetchEntraPreview,
} = useEntraProfileSync()

// Fields shown as default rows when nothing is configured yet.
const ENTRA_DEFAULT_VISIBLE = ['jobTitle', 'department', 'companyName', 'officeLocation']

const entraEnabled = ref(false)
const shownFields = ref<string[]>([...ENTRA_DEFAULT_VISIBLE])
const selected = ref<Set<string>>(new Set(ENTRA_DEFAULT_VISIBLE))
const fieldToAdd = ref('')
const entraSavedFlash = ref(false)
const hasFetchedEntra = ref(false)

const fieldLabel = (f: string) => {
  const key = `settings.identityProvider.field_${f}`
  const translated = t(key)
  return translated === key ? f : translated
}

const sampleValue = (f: string): string => {
  const v = preview.value?.samples?.[f]
  if (v === null || v === undefined || v === '') return ''
  if (typeof v === 'object') {
    return Object.entries(v)
      .filter(([, val]) => val !== null && val !== '')
      .map(([k, val]) => `${k}=${val}`)
      .join(', ')
  }
  return String(v)
}

// Allowlisted fields not currently shown → available to add.
const addableFields = computed(() =>
  (preview.value?.allowed_fields || ENTRA_PROFILE_FIELDS).filter(f => !shownFields.value.includes(f))
)

const toggleField = (f: string) => {
  const next = new Set(selected.value)
  if (next.has(f)) next.delete(f)
  else next.add(f)
  selected.value = next
}

const removeField = (f: string) => {
  shownFields.value = shownFields.value.filter(x => x !== f)
  const next = new Set(selected.value)
  next.delete(f)
  selected.value = next
}

const addField = () => {
  const f = fieldToAdd.value
  if (!f || shownFields.value.includes(f)) return
  shownFields.value = [...shownFields.value, f]
  selected.value = new Set([...selected.value, f])
  fieldToAdd.value = ''
}

const applyEntraConfig = () => {
  if (!entraConfig.value) return
  entraEnabled.value = !!entraConfig.value.enabled
  const configured = entraConfig.value.fields || []
  // Show configured fields plus the default set, so the admin always sees the
  // common attributes even before selecting any.
  const union = Array.from(new Set([...ENTRA_DEFAULT_VISIBLE, ...configured]))
  shownFields.value = union
  selected.value = new Set(configured.length ? configured : ENTRA_DEFAULT_VISIBLE)
}

const handleEntraSave = async () => {
  entraSavedFlash.value = false
  const fields = shownFields.value.filter(f => selected.value.has(f))
  const ok = await saveEntra({ enabled: entraEnabled.value, fields })
  if (ok) {
    applyEntraConfig()
    entraSavedFlash.value = true
    setTimeout(() => { entraSavedFlash.value = false }, 3000)
  }
}

const initEntra = async () => {
  if (hasFetchedEntra.value) return
  hasFetchedEntra.value = true
  await fetchEntraConfig()
  applyEntraConfig()
  // Live sample values from the admin's own profile (best-effort).
  fetchEntraPreview()
}

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

const _df = useFormatDate()
const formatRelativeTime = (timestamp: string | null) => {
  if (!timestamp) return ''
  const isoTimestamp = timestamp.endsWith('Z') ? timestamp : timestamp + 'Z'
  const date = new Date(isoTimestamp)
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`

  return _df.format(date, { month: 'short', day: 'numeric' })
}

// ── Init ──
// Entra profile sync and SSO are not enterprise-gated — load immediately.
onMounted(() => { initEntra(); initSso() })

watch(
  () => license.value,
  (newLicense) => {
    if (newLicense && hasFeature('scim') && !hasFetchedScim.value) {
      hasFetchedScim.value = true
      fetchTokens()
    }
    if (newLicense && hasFeature('ldap') && !hasFetchedLdap.value) {
      hasFetchedLdap.value = true
      fetchLdapStatus()
      fetchLdapConfig().then(applyLdapConfigToForm)
    }
  },
  { immediate: true }
)
</script>
