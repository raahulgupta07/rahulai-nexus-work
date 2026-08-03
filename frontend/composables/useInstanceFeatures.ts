// Super-admin control over instance-wide feature switches.
//
// Distinct from useAppSettings, which reads the RESOLVED values off the public
// GET /api/settings feed — that is what the app renders against. This composable
// talks to GET/PUT /api/instance/features, which additionally reports where each
// value came from, and is 403 for anyone who is not a super admin.
//
// ★A switch has three states, not two: on, off, and "not chosen" (inheriting the
// deployment's default). Saving `null` clears the override rather than writing
// false — writing false would pin the switch off and make the default
// unreachable, which is the whole reason the backend keeps a tri-state.

export type FeatureState = {
  value: boolean
  source: 'db' | 'default'
  default: boolean
}

export const useInstanceFeatures = () => {
  const features = useState<Record<string, FeatureState> | null>('instance-features', () => null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  // The gate is `is_superuser` — an instance-wide flag, deliberately NOT the
  // `manage_settings` permission every other Settings screen uses. An org admin
  // administers their organization; these switches change the product for every
  // organization on the deployment.
  const { data: session } = useAuth()
  const isSuperAdmin = computed(() => Boolean((session.value as any)?.is_superuser))

  const fetchFeatures = async () => {
    if (!isSuperAdmin.value) return
    loading.value = true
    error.value = ''
    try {
      const res = await useMyFetch('/api/instance/features')
      if (res.data.value) features.value = res.data.value as Record<string, FeatureState>
    } catch (e: any) {
      error.value = e?.message || 'Could not load instance settings'
    } finally {
      loading.value = false
    }
  }

  // `value: null` resets to the deployment default. See the note above.
  const setFeature = async (name: string, value: boolean | null) => {
    saving.value = true
    error.value = ''
    try {
      const res = await useMyFetch(`/api/instance/features/${name}`, {
        method: 'PUT',
        body: { value },
      })
      if (res.data.value) {
        features.value = { ...(features.value || {}), [name]: res.data.value as FeatureState }
        // The public feed is cached in useAppSettings and now carries a stale
        // value — the nav item would keep its old state until a hard reload.
        await useAppSettings().refresh()
      }
      return true
    } catch (e: any) {
      error.value = e?.message || 'Could not save'
      return false
    } finally {
      saving.value = false
    }
  }

  return { features, loading, saving, error, isSuperAdmin, fetchFeatures, setFeature }
}
