// SSO Providers Management Composable
// Licensed under the Business Source License 1.1

// Read shape for a single provider. Client secret is never returned —
// only client_secret_set signals whether one is stored.
export type SsoProvider = {
  name: string
  enabled: boolean
  issuer: string
  client_id: string
  client_secret_set: boolean
  scopes: string[]
  label: string
  icon: string
  pkce: boolean
  discovery: boolean
  uid_claim: string
  sync_groups: boolean
  group_claim: string
  resolve_group_names: boolean
  // Anyone this provider vouches for gets an account, with no invite.
  auto_provision: boolean
}

// Google is an OAuth provider (no issuer). Secret never returned.
export type SsoGoogle = {
  enabled: boolean
  client_id: string
  client_secret_set: boolean
  auto_provision: boolean
}

export type SsoAuthMode = 'hybrid' | 'local_only' | 'sso_only'

// Read shape of the whole SSO config.
export type SsoConfig = {
  auth_mode: SsoAuthMode
  google: SsoGoogle
  providers: SsoProvider[]
  source_db: boolean
}

// Write shape: client_secret is optional (only sent when changed); omit to
// keep the stored one. auth_mode / google / providers are all optional so a
// partial update (e.g. just the auth mode) is possible.
export type SsoGoogleUpdate = {
  enabled: boolean
  client_id: string
  client_secret?: string
}

export type SsoProviderUpdate = Omit<SsoProvider, 'client_secret_set'> & {
  client_secret?: string
}

export type SsoConfigUpdate = {
  auth_mode?: SsoAuthMode
  google?: SsoGoogleUpdate
  providers?: SsoProviderUpdate[]
}

export const useSsoProviders = () => {
  const config = ref<SsoConfig | null>(null)
  const saving = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const fetchConfig = async (): Promise<SsoConfig | null> => {
    loading.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/sso/config')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to fetch SSO config'
        throw new Error(msg)
      }
      config.value = res.data.value as SsoConfig
      return config.value
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch SSO config'
      return null
    } finally {
      loading.value = false
    }
  }

  const saveConfig = async (payload: SsoConfigUpdate): Promise<SsoConfig | null> => {
    saving.value = true
    error.value = null
    try {
      const res = await useMyFetch('/api/enterprise/sso/config', {
        method: 'PUT',
        body: payload,
      })
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to save SSO config'
        throw new Error(msg)
      }
      config.value = res.data.value as SsoConfig
      return config.value
    } catch (e: any) {
      error.value = e.message || 'Failed to save SSO config'
      return null
    } finally {
      saving.value = false
    }
  }

  return {
    config,
    saving,
    loading,
    error,
    fetchConfig,
    saveConfig,
  }
}
