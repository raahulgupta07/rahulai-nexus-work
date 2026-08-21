// LDAP Sync Management Composable
// Licensed under the Business Source License 1.1

export type SyncResult = {
  groups_created: number
  groups_updated: number
  groups_removed: number
  memberships_added: number
  memberships_removed: number
  users_not_found: number
  errors: string[]
  timestamp: string | null
}

export type SyncStatus = {
  last_sync: SyncResult | null
  is_syncing: boolean
  ldap_configured: boolean
}

export type LDAPGroupPreview = {
  dn: string
  name: string
  member_count: number
  exists_in_app: boolean
  members_to_add: number
  members_to_remove: number
}

export type LDAPSyncPreview = {
  groups_to_create: number
  groups_to_update: number
  groups_to_remove: number
  total_membership_changes: number
  groups: LDAPGroupPreview[]
  // When groups_read is false the counts above are "not measured", NOT zero —
  // the preview answers with what it could see and names what it could not.
  groups_read: boolean
  group_error: string | null
  users_read: boolean
  user_error: string | null
}

export type LDAPTestResult = {
  connected: boolean
  server: string
  vendor: string | null
  error: string | null
  user_count: number | null
  group_count: number | null
  user_error: string | null
  group_error: string | null
}

// Read shape (bind password never returned — only bind_password_set).
export type LDAPConfig = {
  enabled: boolean
  url: string | null
  bind_dn: string | null
  bind_password_set: boolean
  use_ssl: boolean
  start_tls: boolean
  base_dn: string | null
  user_search_base: string | null
  user_search_filter: string
  user_email_attribute: string
  user_login_attribute: string
  user_name_attribute: string
  group_search_base: string | null
  group_search_filter: string
  group_name_attribute: string
  group_member_attribute: string
  group_member_format: string
  sync_interval_minutes: number
  auto_provision_users: boolean
  connection_timeout: number
  page_size: number
  source_db: boolean
}

// Write shape adds the optional plaintext bind_password (only sent when changed).
export type LDAPConfigUpdate = Omit<LDAPConfig, 'bind_password_set' | 'source_db'> & {
  bind_password?: string | null
}

export const useLdapSync = () => {
  const status = ref<SyncStatus | null>(null)
  const preview = ref<LDAPSyncPreview | null>(null)
  const testResult = ref<LDAPTestResult | null>(null)
  const config = ref<LDAPConfig | null>(null)
  const saving = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const fetchConfig = async (): Promise<LDAPConfig | null> => {
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/config')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to fetch LDAP config'
        throw new Error(msg)
      }
      config.value = res.data.value as LDAPConfig
      return config.value
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch LDAP config'
      return null
    }
  }

  const saveConfig = async (payload: LDAPConfigUpdate): Promise<LDAPConfig | null> => {
    saving.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/config', {
        method: 'PUT',
        body: payload,
      })
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to save LDAP config'
        throw new Error(msg)
      }
      config.value = res.data.value as LDAPConfig
      await fetchStatus()
      return config.value
    } catch (e: any) {
      error.value = e.message || 'Failed to save LDAP config'
      return null
    } finally {
      saving.value = false
    }
  }

  const fetchStatus = async () => {
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/sync/status')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to fetch LDAP status'
        throw new Error(msg)
      }
      status.value = res.data.value as SyncStatus
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch LDAP status'
    }
  }

  const triggerSync = async (): Promise<SyncResult | null> => {
    loading.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/sync', {
        method: 'POST',
      })
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Sync failed'
        throw new Error(msg)
      }
      const result = res.data.value as SyncResult
      // Refresh status after sync
      await fetchStatus()
      return result
    } catch (e: any) {
      error.value = e.message || 'Sync failed'
      return null
    } finally {
      loading.value = false
    }
  }

  const fetchPreview = async (): Promise<LDAPSyncPreview | null> => {
    loading.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/sync/preview')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to fetch preview'
        throw new Error(msg)
      }
      preview.value = res.data.value as LDAPSyncPreview
      return preview.value
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch preview'
      return null
    } finally {
      loading.value = false
    }
  }

  const testConnection = async (): Promise<LDAPTestResult | null> => {
    loading.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/ldap/test-connection')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Connection test failed'
        throw new Error(msg)
      }
      testResult.value = res.data.value as LDAPTestResult
      return testResult.value
    } catch (e: any) {
      error.value = e.message || 'Connection test failed'
      return null
    } finally {
      loading.value = false
    }
  }

  return {
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
  }
}
