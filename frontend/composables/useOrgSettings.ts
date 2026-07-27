// /composables/useOrgSettings.ts

type Feature = {
  name: string
  description: string
  is_lab?: boolean
  editable?: boolean
  state?: 'enabled' | 'disabled' | 'locked'
  value?: boolean | number | string | null
}

type OrganizationSettingsResponse = {
  id: string
  organization_id: string
  created_at: string
  updated_at: string
  config: {
    general?: Record<string, any>
    ai_features?: Record<string, Feature>
    allow_llm_see_data?: Feature
    allow_file_upload?: Feature
    allow_code_editing?: Feature
    enable_llm_judgement?: Feature
    [key: string]: any
  }
}

export const useOrgSettings = () => {
  const { organization, ensureOrganization } = useOrganization()

  // Cache settings per organization id
  const key = computed(() => `org-settings-${organization.value?.id || 'none'}`)
  const settings = useState<OrganizationSettingsResponse | null>(key.value, () => null)
  const loading = useState<boolean>(`${key.value}-loading`, () => false)
  const error = useState<string | null>(`${key.value}-error`, () => null)

  const fetchSettings = async () => {
    // Dedupe: ~19 components call useOrgSettings(), each with an immediate
    // watcher. Without this guard every one fires its own
    // GET /api/organization/settings on page load (a request storm). Skip if
    // already cached or in-flight; the shared useState cache means all callers
    // still receive the result.
    if (loading.value || settings.value) return
    error.value = null
    loading.value = true
    try {
      await ensureOrganization()
      const res = await useMyFetch('/api/organization/settings')
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.message || 'Failed to fetch organization settings'
        throw new Error(msg)
      }
      settings.value = res.data.value as OrganizationSettingsResponse
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch organization settings'
    } finally {
      loading.value = false
    }
  }

  // Auto-fetch when org id becomes available
  watch(
    () => organization.value?.id,
    async (newId, oldId) => {
      if (newId && newId !== oldId) {
        await fetchSettings()
      }
    },
    { immediate: true }
  )

  const getFeature = (path: string): Feature | undefined => {
    const cfg = settings.value?.config
    if (!cfg) return undefined
    // direct key first
    if (cfg[path]) return cfg[path] as Feature
    // ai_features map fallback
    const ai = cfg.ai_features || {}
    return ai[path]
  }

  const featureEnabled = (feature?: Feature) => {
    if (!feature) return false
    if (feature.state && feature.state !== 'enabled') return false
    if (typeof feature.value === 'boolean') return feature.value === true
    return feature.state === 'enabled'
  }

  // --- three-state member access (Settings → Access) ---------------------
  // Mirrors the backend's access_state(). featureEnabled() must NOT be used
  // for these: a string value is not a boolean, so it falls through to
  // `state === 'enabled'` — and a FeatureConfig holding "off" still carries
  // state 'enabled', so the feature would read as ON in exactly the case
  // meant to hide it.
  type AccessState = 'on' | 'coming_soon' | 'off'
  const ACCESS_STATES = ['on', 'coming_soon', 'off']

  const accessState = (path: string): AccessState => {
    const value = getFeature(path)?.value
    // Legacy: mcp_enabled shipped as a boolean, and orgs still have `true`
    // stored. That must keep meaning "on".
    if (typeof value === 'boolean') return value ? 'on' : 'off'
    if (typeof value === 'string') {
      const v = value.trim().toLowerCase().replace(/[-\s]/g, '_')
      if (ACCESS_STATES.includes(v)) return v as AccessState
      if (['true', 'enabled', 'yes'].includes(v)) return 'on'
      if (['soon', 'comingsoon'].includes(v)) return 'coming_soon'
    }
    return 'off'  // unset or unreadable → hidden, never fail open
  }

  const mcpAccess = computed<AccessState>(() => accessState('mcp_enabled'))
  const apiKeysAccess = computed<AccessState>(() => accessState('api_keys'))
  const localFoldersAccess = computed<AccessState>(() => accessState('local_folders'))

  const isJudgeEnabled = computed(() => featureEnabled(getFeature('enable_llm_judgement')))
  const canUploadFiles = computed(() => featureEnabled(getFeature('enable_file_upload')))
  const canEditCode = computed(() => featureEnabled(getFeature('enable_code_editing')))
  const isMcpEnabled = computed(() => mcpAccess.value === 'on')
  const isMcpToolsEnabled = computed(() => featureEnabled(getFeature('enable_mcp_tools')))
  const allowLlmSeeData = computed(() => featureEnabled(getFeature('allow_llm_see_data')))
  const isTrainingModeEnabled = computed(() => featureEnabled(getFeature('enable_training_mode')))
  const isFollowUpsEnabled = computed(() => featureEnabled(getFeature('enable_follow_ups')))

  return {
    settings,
    loading,
    error,
    fetchSettings,
    // flags
    isJudgeEnabled,
    canUploadFiles,
    canEditCode,
    isMcpEnabled,
    isMcpToolsEnabled,
    allowLlmSeeData,
    isTrainingModeEnabled,
    isFollowUpsEnabled,
    // three-state member access
    accessState,
    mcpAccess,
    apiKeysAccess,
    localFoldersAccess,
    // raw accessor
    getFeature,
  }
}


