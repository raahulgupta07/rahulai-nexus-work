<!--
  LdapConfigModal — the LDAP Directory Sync form, lifted out of the inline
  accordion on /settings/identity-provider into a dialog.

  Same fields, same payload, same "Save & Test" semantics as the accordion; only
  the container changed. One scrolling page, no left rail, no tabs — the four
  section headings (Connection / Directory tree / Groups / Sync) are the only
  structure.

  LDAP config is PER-ORGANIZATION (stored on OrganizationSettings.config.ldap),
  unlike SSO which is instance-global. The subtitle names the org so that is
  visible rather than assumed.
-->
<template>
  <UModal v-model="isOpen" :ui="{ width: 'sm:max-w-3xl' }">
    <div class="flex flex-col max-h-[85vh]">
      <!-- ── Header ──────────────────────────────────────────────────── -->
      <div class="flex items-start justify-between gap-3 px-5 pt-5 pb-4 border-b border-gray-100 dark:border-gray-800">
        <div class="flex items-start gap-3 min-w-0">
          <ProviderMark icon="ldap" label="LDAP" />
          <div class="min-w-0">
            <h3 class="text-base font-semibold text-gray-900 dark:text-white">
              {{ $t('settings.identityProvider.ldapTitle') }}
            </h3>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {{ scopeSubtitle }}
            </p>
          </div>
        </div>
        <button
          ref="closeButton"
          class="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 flex-shrink-0"
          :aria-label="$t('settings.identityProvider.cancel')"
          @click="close"
        >
          <UIcon name="heroicons-x-mark" class="w-5 h-5" />
        </button>
      </div>

      <!-- ── Body: one scrolling page ────────────────────────────────── -->
      <div class="flex-1 overflow-y-auto px-5 py-4">
        <!-- Master switch -->
        <label class="flex items-center gap-2 mb-5 cursor-pointer">
          <input type="checkbox" v-model="form.enabled" class="rounded border-gray-300 dark:border-gray-600" />
          <span class="text-xs font-medium text-gray-700 dark:text-gray-300">
            {{ $t('settings.identityProvider.ldapEnable') }}
          </span>
        </label>

        <!-- ── Connection ────────────────────────────────────────────── -->
        <p :class="headingClass">{{ $t('settings.identityProvider.ldapSectionConnection') }}</p>
        <div class="mb-6 space-y-3">
          <!-- Long URL: full width -->
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapUrl') }}</label>
            <input
              ref="firstField"
              v-model="form.url"
              type="text"
              placeholder="ldaps://ad.corp.com:636"
              :class="inputClass"
            />
          </div>

          <div class="flex flex-wrap items-center gap-4">
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="form.use_ssl" class="rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapUseSsl') }}</span>
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="form.start_tls" class="rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapStartTls') }}</span>
            </label>
            <label class="flex items-center gap-2">
              <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapTimeout') }}</span>
              <input v-model.number="form.connection_timeout" type="number" min="1" class="w-16 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </label>
          </div>

          <!-- Long DN: full width -->
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapBindDn') }}</label>
            <input v-model="form.bind_dn" type="text" placeholder="cn=svc-dash,ou=svc,dc=corp,dc=com" :class="inputClass" />
          </div>

          <!-- Write-only: blank keeps the saved (encrypted) password. -->
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapBindPassword') }}</label>
            <input
              v-model="form.bind_password"
              type="password"
              autocomplete="new-password"
              :placeholder="bindPasswordSet ? $t('settings.identityProvider.ldapPasswordSaved') : ''"
              :class="inputClass"
            />
            <p :class="hintClass">{{ $t('settings.identityProvider.ldapPasswordHint') }}</p>
          </div>
        </div>

        <!-- ── Directory tree ────────────────────────────────────────── -->
        <p :class="headingClass">{{ $t('settings.identityProvider.ldapSectionTree') }}</p>
        <div class="mb-6 space-y-3">
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapBaseDn') }}</label>
            <input v-model="form.base_dn" type="text" placeholder="dc=corp,dc=com" :class="inputClass" />
          </div>
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapUserSearchBase') }}</label>
            <input v-model="form.user_search_base" type="text" :placeholder="$t('settings.identityProvider.ldapDefaultsBaseDn')" :class="inputClass" />
          </div>
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapUserFilter') }}</label>
            <input v-model="form.user_search_filter" type="text" :class="inputClass" />
          </div>
          <!-- Short paired attributes: two columns -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapEmailAttr') }}</label>
              <input v-model="form.user_email_attribute" type="text" :class="inputClass" />
            </div>
            <!-- What people TYPE at the sign-in form; the email attribute is what
                 the account is keyed on. Added alongside the LDAP door. -->
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapLoginAttr') }}</label>
              <input v-model="form.user_login_attribute" type="text" placeholder="uid" :class="inputClass" />
              <p :class="hintClass">{{ $t('settings.identityProvider.ldapLoginAttrHint') }}</p>
            </div>
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapNameAttr') }}</label>
              <input v-model="form.user_name_attribute" type="text" :class="inputClass" />
            </div>
          </div>
        </div>

        <!-- ── Groups ────────────────────────────────────────────────── -->
        <p :class="headingClass">{{ groupsHeading }}</p>
        <div class="mb-6 space-y-3">
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapGroupSearchBase') }}</label>
            <input v-model="form.group_search_base" type="text" :placeholder="$t('settings.identityProvider.ldapDefaultsBaseDn')" :class="inputClass" />
          </div>
          <div>
            <label :class="labelClass">{{ $t('settings.identityProvider.ldapGroupFilter') }}</label>
            <input v-model="form.group_search_filter" type="text" :class="inputClass" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapGroupNameAttr') }}</label>
              <input v-model="form.group_name_attribute" type="text" :class="inputClass" />
            </div>
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapMemberAttr') }}</label>
              <input v-model="form.group_member_attribute" type="text" :class="inputClass" />
            </div>
            <div>
              <label :class="labelClass">{{ $t('settings.identityProvider.ldapMemberFormat') }}</label>
              <select v-model="form.group_member_format" :class="inputClass">
                <option value="dn">dn</option>
                <option value="uid">uid</option>
              </select>
            </div>
          </div>
        </div>

        <!-- ── Sync ──────────────────────────────────────────────────── -->
        <p :class="headingClass">{{ $t('settings.identityProvider.ldapSectionSync') }}</p>
        <div class="space-y-3">
          <div class="flex flex-wrap items-center gap-4">
            <label class="flex items-center gap-2">
              <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapInterval') }}</span>
              <input v-model.number="form.sync_interval_minutes" type="number" min="1" class="w-16 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
              <span class="text-[11px] text-gray-400 dark:text-gray-500">{{ $t('settings.identityProvider.ldapMinutes') }}</span>
            </label>
            <label class="flex items-center gap-2">
              <span class="text-[11px] text-gray-500 dark:text-gray-400">{{ $t('settings.identityProvider.ldapPageSize') }}</span>
              <input v-model.number="form.page_size" type="number" min="1" class="w-20 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200" />
            </label>
            <label class="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" v-model="form.auto_provision_users" class="rounded border-gray-300 dark:border-gray-600" />
              <span class="text-[11px] text-gray-600 dark:text-gray-300">{{ $t('settings.identityProvider.ldapAutoProvision') }}</span>
            </label>
          </div>

          <!-- Status + run-sync affordance, same as the inline form. -->
          <template v-if="form.enabled || status?.ldap_configured">
            <div v-if="status?.last_sync" class="rounded border border-gray-200 dark:border-gray-700 p-3">
              <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.lastSync') }}</span>
              <p class="text-[11px] text-gray-400 dark:text-gray-400 mt-0.5">
                {{ status.last_sync.timestamp ? formatRelativeTime(status.last_sync.timestamp) : $t('settings.identityProvider.unknown') }}
                {{ $t('settings.identityProvider.lastSyncDetail', {
                  gCreated: status.last_sync.groups_created,
                  gUpdated: status.last_sync.groups_updated,
                  gRemoved: status.last_sync.groups_removed,
                  mAdded: status.last_sync.memberships_added,
                  mRemoved: status.last_sync.memberships_removed,
                }) }}
              </p>
              <p v-if="status.last_sync.errors.length" class="text-[11px] text-red-400 mt-0.5">
                {{ $t('settings.identityProvider.errorCount', { n: status.last_sync.errors.length, first: status.last_sync.errors[0] }) }}
              </p>
            </div>

            <div class="flex items-center gap-2">
              <button
                class="px-2 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
                :disabled="loading"
                @click="handleSync"
              >
                {{ loading ? $t('settings.identityProvider.syncing') : $t('settings.identityProvider.syncNow') }}
              </button>
              <button
                class="px-2 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 disabled:opacity-50"
                :disabled="loading"
                @click="handlePreview"
              >
                {{ $t('settings.identityProvider.previewChanges') }}
              </button>
            </div>

            <div v-if="lastSyncResult" class="rounded border border-green-200 bg-green-50 dark:bg-green-950 p-3">
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

            <div v-if="preview" class="rounded border border-gray-200 dark:border-gray-700 overflow-hidden">
              <div class="bg-gray-50 dark:bg-gray-900 px-3 py-2 border-b border-gray-200 dark:border-gray-700">
                <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ $t('settings.identityProvider.preview') }} </span>
                <span class="text-xs text-gray-500 dark:text-gray-400">
                  {{ $t('settings.identityProvider.previewSummary', {
                    create: preview.groups_to_create,
                    update: preview.groups_to_update,
                    remove: preview.groups_to_remove,
                    changes: preview.total_membership_changes,
                  }) }}
                </span>
              </div>
              <div v-if="preview.groups.length" class="max-h-48 overflow-y-auto">
                <div
                  v-for="(group, idx) in preview.groups"
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
          </template>

          <p v-if="config && !config.source_db" class="text-[11px] text-gray-400 dark:text-gray-500">
            {{ $t('settings.identityProvider.ldapFromFile') }}
          </p>
        </div>
      </div>

      <!-- ── Footer: Cancel · Test connection · Save ─────────────────── -->
      <div class="px-5 py-3 border-t border-gray-100 dark:border-gray-800 flex items-center gap-3">
        <!-- Inline result, never an alert(). -->
        <div class="flex-1 min-w-0 text-[11px]">
          <p v-if="error" class="text-red-500 truncate" :title="error">{{ error }}</p>
          <template v-else-if="testResult">
            <p v-if="testResult.connected" class="text-green-600 truncate">
              {{ $t('settings.identityProvider.statusConnected') }}
              <template v-if="testResult.server"> · {{ testResult.server }}</template>
              <template v-if="testResult.vendor"> · {{ testResult.vendor }}</template>
              <template v-if="testResult.user_count !== null"> · {{ testResult.user_count }} users</template>
              <template v-if="testResult.group_count !== null"> · {{ testResult.group_count }} groups</template>
            </p>
            <p v-else class="text-red-500 truncate" :title="testResult.error || ''">
              {{ $t('settings.identityProvider.statusFailed') }}<template v-if="testResult.error"> · {{ testResult.error }}</template>
            </p>
          </template>
          <p v-else-if="savedFlash" class="text-green-600">{{ $t('settings.identityProvider.saved') }}</p>
        </div>

        <button
          class="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300"
          @click="close"
        >
          {{ $t('settings.identityProvider.cancel') }}
        </button>
        <button
          class="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded hover:border-gray-300 disabled:opacity-50"
          :disabled="loading || saving"
          @click="handleTestConnection"
        >
          {{ loading ? $t('settings.identityProvider.testing') : $t('settings.identityProvider.testConnection') }}
        </button>
        <button
          class="px-3 py-1.5 text-xs text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50"
          :disabled="saving"
          @click="handleSave"
        >
          {{ saving ? $t('settings.identityProvider.saving') : $t('settings.identityProvider.save') }}
        </button>
      </div>
    </div>
  </UModal>
</template>

<script setup lang="ts">
import ProviderMark from '~/components/settings/ProviderMark.vue'
import { useLdapSync, type SyncResult as LDAPSyncResult } from '~/ee/composables/useLdapSync'

const props = defineProps<{
  modelValue: boolean
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'saved'): void
}>()

const isOpen = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const { t, te } = useI18n()
const { organization } = useOrganization()
const { relativeTime: formatRelativeTime } = useRelativeTime()

const {
  status,
  preview,
  testResult,
  config,
  saving,
  loading,
  error,
  fetchConfig,
  saveConfig,
  fetchStatus,
  triggerSync,
  fetchPreview,
  testConnection,
} = useLdapSync()

// Shared input chrome, so the two-column and full-width fields cannot drift.
const inputClass = 'w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-200'
const labelClass = 'block text-[11px] text-gray-500 dark:text-gray-400 mb-1'
const hintClass = 'mt-1 text-[10px] text-gray-400 dark:text-gray-500'
const headingClass = 'text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2'

// Two keys this modal introduces; fall back to English until they land in
// locales/en.json so a missing key never renders as a raw dotted path.
const tr = (key: string, fallback: string) =>
  te(`settings.identityProvider.${key}`) ? t(`settings.identityProvider.${key}`) : fallback

const groupsHeading = computed(() => tr('ldapSectionGroups', 'Groups'))
const scopeSubtitle = computed(() => {
  const org = organization.value?.name
  return org
    ? (te('settings.identityProvider.ldapScopeOrg')
        ? t('settings.identityProvider.ldapScopeOrg', { org })
        : `Directory sync for ${org} — this configuration applies to this organization only`)
    : t('settings.identityProvider.ldapSubtitle')
})

// Editable form model. bind_password is write-only: blank keeps the saved
// (encrypted) value; typing a value re-sets it. bindPasswordSet only reflects
// whether one is already stored, so the field can show a "saved" placeholder.
// Defaults mirror OrgLdapUpdate exactly.
const form = reactive({
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
  user_login_attribute: 'uid',
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
const bindPasswordSet = ref(false)
const lastSyncResult = ref<LDAPSyncResult | null>(null)
const savedFlash = ref(false)

const applyConfigToForm = () => {
  const c = config.value
  if (!c) return
  form.enabled = c.enabled
  form.url = c.url || ''
  form.bind_dn = c.bind_dn || ''
  form.bind_password = ''
  form.use_ssl = c.use_ssl
  form.start_tls = c.start_tls
  form.base_dn = c.base_dn || ''
  form.user_search_base = c.user_search_base || ''
  form.user_search_filter = c.user_search_filter
  form.user_email_attribute = c.user_email_attribute
  form.user_login_attribute = c.user_login_attribute
  form.user_name_attribute = c.user_name_attribute
  form.group_search_base = c.group_search_base || ''
  form.group_search_filter = c.group_search_filter
  form.group_name_attribute = c.group_name_attribute
  form.group_member_attribute = c.group_member_attribute
  form.group_member_format = c.group_member_format
  form.sync_interval_minutes = c.sync_interval_minutes
  form.auto_provision_users = c.auto_provision_users
  form.connection_timeout = c.connection_timeout
  form.page_size = c.page_size
  bindPasswordSet.value = c.bind_password_set
}

const buildPayload = () => {
  const p: any = { ...form }
  // Only send bind_password when the user typed a new one — an empty string
  // would overwrite the stored secret with a blank.
  if (!form.bind_password) delete p.bind_password
  return p
}

const handleSave = async () => {
  const saved = await saveConfig(buildPayload())
  if (saved) {
    applyConfigToForm()
    savedFlash.value = true
    setTimeout(() => { savedFlash.value = false }, 4000)
    emit('saved')
  }
  return saved
}

const handleTestConnection = async () => {
  // The backend tests the SAVED config, so persist the form first — same order
  // as the inline "Save & Test" button.
  testResult.value = null
  const saved = await handleSave()
  if (!saved) return
  await testConnection()
}

const handleSync = async () => {
  lastSyncResult.value = null
  preview.value = null
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

const close = () => { isOpen.value = false }

// Load fresh on every open — the config may have been changed elsewhere — and
// clear the transient result strips so a previous session's outcome does not
// read as this one's.
const firstField = ref<HTMLInputElement | null>(null)
watch(isOpen, async (open) => {
  if (!open) return
  testResult.value = null
  preview.value = null
  lastSyncResult.value = null
  savedFlash.value = false
  error.value = null
  await fetchConfig()
  applyConfigToForm()
  await fetchStatus()
  await nextTick()
  firstField.value?.focus()
}, { immediate: true })
</script>
