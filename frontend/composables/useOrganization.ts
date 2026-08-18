// /composables/useOrganization.ts
const STORAGE_KEY = 'bow.selectedOrganizationId'

// In-flight session fetch, shared across every concurrent caller. Module-level
// on purpose: useOrganization() is re-invoked per call site, so state inside
// the composable would not be shared. Every useMyFetch awaits
// ensureOrganization(), and at boot dozens of fetches start before the first
// session response lands — without this each of them saw `id === null` and
// issued its own whoami (measured: duplicate whoami on every page load, each
// response triggering its own re-render wave).
let orgSessionInflight: Promise<any> | null = null

export const useOrganization = () => {
  const { getSession, data: sessionData } = useAuth()
  // Initialize with null to indicate not loaded yet
  const organization = useState('organization', () => ({
    id: null as string | null,
    name: '',
  }))

  const readPersistedOrgId = (): string | null => {
    if (!process.client) return null
    try { return localStorage.getItem(STORAGE_KEY) } catch { return null }
  }

  const writePersistedOrgId = (id: string | null) => {
    if (!process.client) return
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch {}
  }

  // Fetch organization from session data.
  // Reuse the session already loaded by the auth middleware / permissions
  // plugin instead of forcing a fresh whoami — this used to fire an extra
  // (forced) whoami on every boot on top of the one just fetched. Only hit the
  // network when we genuinely have no session yet.
  const fetchOrganizationFromSession = async () => {
    const cached = sessionData?.value as any
    const session = (cached && cached.organizations) ? cached : await getSession()
    const orgs = session?.organizations || []
    if (orgs.length > 0) {
      const persistedId = readPersistedOrgId()
      const match = persistedId ? orgs.find((o: any) => o.id === persistedId) : null
      const chosen = match || orgs[0]
      organization.value.id = chosen.id
      organization.value.name = chosen.name

      // ★★★Write the choice back the first time it is made.
      //
      // Falling through to `orgs[0]` without persisting it makes the active
      // workspace a function of whatever order the server happened to return —
      // and `get_user_organizations` had no ORDER BY, so Postgres was free to
      // answer differently on the next load. The user then opens a report in
      // one workspace and, after a refresh, asks for it while a DIFFERENT one
      // is active: the report exists but not in that workspace, so every
      // request under it 404s and the page renders as though their work were
      // gone. Observed in production as bursts of 404 on /api/reports/<id>,
      // /layouts, /artifacts/report/<id> and the rest, clearing on a later
      // reload.
      //
      // The query is ordered now, which fixes the server half. This fixes the
      // client half, and matters most where there is nothing to fall back on:
      // a private window starts with an empty localStorage every time, which
      // is exactly how the fault was reported.
      //
      // ★When a persisted id no longer resolves — the person was removed from
      // that workspace, or it was deleted — the fallback is correct, but the
      // stale id must be replaced rather than left to miss on every load.
      if (!match) {
        if (persistedId) {
          console.warn(
            `[organization] workspace ${persistedId} is no longer available; ` +
            `switching to ${chosen.name}`,
          )
        }
        writePersistedOrgId(chosen.id)
      }
    }
    return organization.value
  }

  // Ensure organization is set. Concurrent callers share one session fetch.
  const ensureOrganization = async () => {
    if (!organization.value?.id) {
      if (!orgSessionInflight) {
        orgSessionInflight = fetchOrganizationFromSession()
          .finally(() => { orgSessionInflight = null })
      }
      await orgSessionInflight
    }
    return organization.value
  }

  // Fetch organization without redirecting
  const fetchOrganization = async () => {
    if (!organization.value?.id) {
      await fetchOrganizationFromSession()
    }
    return organization.value
  }

  // Switch active organization and reload so all org-scoped state is rebuilt
  const setOrganization = (orgId: string) => {
    if (!orgId || orgId === organization.value?.id) return
    writePersistedOrgId(orgId)
    if (process.client) {
      window.location.href = '/'
    }
  }

  return {
    organization,
    ensureOrganization,
    fetchOrganization,
    fetchOrganizationFromSession,
    setOrganization,
  }
}
