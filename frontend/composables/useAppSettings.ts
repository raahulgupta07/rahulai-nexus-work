// Fetches and caches app-level settings from GET /api/settings
// (distinct from org-level settings in useOrgSettings)

type AppSettings = {
  smtp_enabled: boolean
  version: string
  environment: string
  base_url: string
  [key: string]: any
}

export const useAppSettings = () => {
  const settings = useState<AppSettings | null>('app-settings', () => null)
  const loading = useState<boolean>('app-settings-loading', () => false)

  const fetchSettings = async () => {
    if (settings.value) return // already cached
    if (loading.value) return // in-flight: dedupe concurrent callers on cold load
    loading.value = true
    try {
      const res = await useMyFetch('/api/settings')
      if (res.data.value) {
        settings.value = res.data.value as AppSettings
      }
    } catch {
      // Silent fail — features degrade gracefully
    } finally {
      loading.value = false
    }
  }

  const smtpEnabled = computed(() => settings.value?.smtp_enabled ?? false)

  // "Improve overview" feature flag (backend INSTRUCTION_IMPROVE). Default false
  // → the ✨ Improve button stays hidden and the app is unchanged.
  const improveOn = computed(() => settings.value?.features?.instruction_improve ?? false)

  // Per-user private instructions (backend PER_USER_INSTRUCTIONS). Default false
  // → the Private/Shared toggle stays hidden and instructions are shared as today.
  const perUserInstructionsOn = computed(() => settings.value?.features?.per_user_instructions ?? false)

  // Per-user table selection + training on per-user connectors (fabric_user,
  // powerbi_user). Backend HYBRID_PER_USER_TABLE_SELECT. Default false → shared
  // catalog, creator-manages (as today).
  const perUserTableSelectOn = computed(() => settings.value?.features?.per_user_table_select ?? false)

  // Live "Learn agent" progress drawer (backend LEARN_PROGRESS). Default false
  // → the bare "Learning…" spinner stays and the app is unchanged.
  const learnProgressOn = computed(() => settings.value?.features?.learn_progress ?? false)

  // Full-app analytics page (backend HYBRID_APP_ANALYTICS). Default false →
  // the "App Analytics" nav item below Monitoring stays hidden.
  const appAnalyticsOn = computed(() => settings.value?.features?.app_analytics ?? false)

  // Local compute (Option A): "Local folder (this device)" + in-browser DuckDB-WASM
  // execution (backend HYBRID_LOCAL_COMPUTE). Default false → connector hidden.
  const localComputeOn = computed(() => settings.value?.features?.local_compute ?? false)

  // Local runtime (Cowork-style): paired helper runs the agent's Python on the
  // user's laptop (backend HYBRID_LOCAL_RUNTIME). Default false → settings
  // section hidden, all execution stays server-side.
  const localRuntimeOn = computed(() => settings.value?.features?.local_runtime ?? false)

  // Attach a local folder in chat (backend HYBRID_LOCAL_FOLDER_ATTACH). Rides on
  // top of the local runtime. Default false → the composer's attach button keeps
  // its current click-to-upload behavior and no folder UI exists.
  const localFolderAttachOn = computed(() => settings.value?.features?.local_folder_attach ?? false)

  // Auto-fetch on first use
  if (!settings.value && !loading.value) {
    fetchSettings()
  }

  return {
    settings,
    loading,
    smtpEnabled,
    improveOn,
    perUserInstructionsOn,
    perUserTableSelectOn,
    learnProgressOn,
    appAnalyticsOn,
    localComputeOn,
    localRuntimeOn,
    localFolderAttachOn,
    fetchSettings,
  }
}
