// Handing your own work to a colleague.
//
// Every call here is scoped to the SIGNED-IN USER's own content — the backend
// selects on `Report.user_id == caller` rather than validating an id list, so
// there is nothing this composable could ask for that would reach somebody
// else's work. The admin equivalent is a separate surface and a separate
// release.
//
// ★No auto-fetch. `useAppSettings` fetches on creation and `useInstanceFeatures`
// does not, and the difference has bitten this codebase before: a page that
// destructures state and never calls the fetcher renders nothing, with no
// error anywhere, and the section simply does not appear. Callers here must
// call `fetchContent()` themselves — the profile modal does it when its tab is
// opened, so the request only happens for someone who went looking.

export type ContentSummary = {
  reports: number
  // ★A sub-count OF `reports`, and like the two below it is NOT in `total` —
  // adding it would count the same rows twice. Reports that are nothing but a
  // chat thread: no dashboard, no schedule, no share. The automatic paths
  // (successor handover, admin offboarding) deliberately LEAVE these behind,
  // because releases .9/.10 gated reports `owner_only` as conversation privacy
  // and handing somebody else's private chats to a colleague cannot also be
  // right. Surfaced so a dialog can say what will NOT move — a line naming only
  // what moves reads as a complete promise, and that is how somebody later asks
  // where their chats went.
  conversation_reports: number
  artifacts: number
  queries: number
  scheduled_prompts: number
  projects: number
  // The agent's per-report working notes. A child of a report, so it always
  // moves with one.
  notes: number
  // SHARED instructions and NON-PRIVATE prompts only — a private item is a
  // note-to-self and stays with the person. The count is the rows a transfer
  // will actually move, so the screen and the batch can never disagree.
  instructions: number
  prompts: number
  // Agents this person owns. Ownership only: a transfer never changes who can
  // see or use an agent.
  data_sources: number
  // Reports whose queries run as their OWNER, using that person's data-source
  // sign-in. Surfaced separately because a transfer changes whose credentials
  // the dashboard uses, and that has to be said before someone confirms.
  creator_identity_reports: number
  // ★A sub-count OF `data_sources`, and like `creator_identity_reports` it is
  // NOT in `total` — adding it would double-count rows already counted. Agents
  // on a per-user data source, where owning the agent is a capability: after
  // the move a Fabric / Power BI agent stops answering until the recipient
  // signs in themselves, and every other per-user kind instead hands them the
  // system sign-in. Both need saying out loud before anyone confirms, which is
  // what `transfer-credentials-warning` does.
  credential_bound_data_sources: number
  total: number
}

export type OwnedItem = {
  id: string
  kind: string
  title: string | null
  type_label: string | null
  shared_with_count: number
  has_schedule: boolean
  runs_as_owner: boolean
  updated_at: string | null
}

export type Successor = {
  successor_user_id: string | null
  successor_name: string | null
  successor_email: string | null
}

export type TransferResult = {
  batch_id: string
  moved: Record<string, number>
  total: number
  creator_identity_repointed: number
  // How many of the moved agents were credential-bound — reported after the
  // fact, so a result can say which agents now need attention rather than only
  // that the batch succeeded.
  credential_bound_data_sources: number
  // ★How many reports were NOT moved because they are pure conversation —
  // measured, after the fact, by the batch that ran. Distinct from the summary's
  // `conversation_reports`, which is the estimate shown BEFORE anyone confirms:
  // this is the one to quote in a confirmation, or the screen will report a
  // number that stopped being true the moment the transfer happened.
  // ★Nonzero only on the automatic paths. Saying it out loud is the point: a
  // batch reporting "moved 12" for somebody who owned 47 reads as a
  // half-finished job, and an admin who cannot tell "left on purpose" from
  // "failed" goes hunting for the other 35.
  conversations_left_behind: number
}

export type OrphanedOwner = {
  user_id: string
  membership_id: string
  name: string
  email: string | null
  // Set means the automatic handover ran and was refused — normally because the
  // successor has since left too.
  successor_name: string | null
  summary: ContentSummary
}

export const useOwnership = () => {
  const summary = ref<ContentSummary | null>(null)
  const items = ref<OwnedItem[]>([])
  const successor = ref<Successor | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')

  // The batch of the last transfer, so the caller can offer Undo without
  // fetching anything. Cleared once undone or once a new transfer replaces it.
  const lastBatchId = ref<string | null>(null)

  const fetchContent = async () => {
    loading.value = true
    error.value = ''
    try {
      const [s, l] = await Promise.all([
        useMyFetch('/api/me/content/summary'),
        useMyFetch('/api/me/content'),
      ])
      if (s.data.value) summary.value = s.data.value as ContentSummary
      if (l.data.value) items.value = l.data.value as OwnedItem[]
    } catch (e: any) {
      error.value = e?.message || 'Could not load your content'
    } finally {
      loading.value = false
    }
  }

  const fetchSuccessor = async () => {
    try {
      const res = await useMyFetch('/api/me/successor')
      if (res.data.value) successor.value = res.data.value as Successor
    } catch {
      // A successor nobody has set is the normal case, not an error worth
      // showing. Leaving it null renders the empty picker.
      successor.value = null
    }
  }

  const saveSuccessor = async (userId: string | null) => {
    saving.value = true
    error.value = ''
    try {
      const res = await useMyFetch('/api/me/successor', {
        method: 'PUT',
        body: { successor_user_id: userId },
      })
      if (res.data.value) successor.value = res.data.value as Successor
      return true
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not save'
      return false
    } finally {
      saving.value = false
    }
  }

  // `reportIds` empty/omitted means everything. `keepAccess` leaves the
  // previous owner a share so they can still open and edit what they handed
  // over — handing over is not the same as walking away, and a handover that
  // instantly locks you out of your own work is one people avoid doing.
  const handOver = async (
    toUserId: string,
    opts: { reportIds?: string[]; keepAccess?: boolean; includeProjects?: boolean } = {},
  ): Promise<TransferResult | null> => {
    saving.value = true
    error.value = ''
    try {
      const res = await useMyFetch('/api/me/content/transfer', {
        method: 'POST',
        body: {
          to_user_id: toUserId,
          report_ids: opts.reportIds && opts.reportIds.length ? opts.reportIds : null,
          keep_access: opts.keepAccess !== false,
          include_projects: opts.includeProjects !== false,
        },
      })
      const result = res.data.value as TransferResult | null
      if (result) {
        lastBatchId.value = result.batch_id
        await fetchContent()
      }
      return result
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not hand over'
      return null
    } finally {
      saving.value = false
    }
  }

  const handOverReport = async (
    reportId: string,
    toUserId: string,
    keepAccess = true,
  ): Promise<TransferResult | null> => {
    saving.value = true
    error.value = ''
    try {
      const res = await useMyFetch(`/api/reports/${reportId}/transfer`, {
        method: 'POST',
        body: { to_user_id: toUserId, keep_access: keepAccess },
      })
      const result = res.data.value as TransferResult | null
      if (result) lastBatchId.value = result.batch_id
      return result
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not hand over'
      return null
    } finally {
      saving.value = false
    }
  }

  // ── admin: acting on somebody else's content ────────────────────────────
  // Separate calls, not a flag on the ones above, because they are a different
  // act with a different gate. The member calls need no permission string —
  // their query is the authorization. These are `full_admin_access`, and
  // keeping them visibly apart is what stops the two rules drifting together.

  const fetchMemberSummary = async (
    organizationId: string,
    membershipId: string,
  ): Promise<ContentSummary | null> => {
    try {
      const res = await useMyFetch(
        `/organizations/${organizationId}/members/${membershipId}/content-summary`,
      )
      return (res.data.value as ContentSummary) || null
    } catch {
      // The Owns column is decoration on a working members list. A failure here
      // must render as "unknown", never as a broken page.
      return null
    }
  }

  // Work whose owner can no longer sign in. An empty array on failure, for the
  // same reason as the summary above: this renders a banner on a working
  // members list, and a broken fetch must not take the page down with it. The
  // cost of that choice is that "nothing stranded" and "could not ask" look
  // identical — acceptable for a prompt, and the reason no count elsewhere is
  // derived from this call.
  const fetchOrphans = async (organizationId: string): Promise<OrphanedOwner[]> => {
    try {
      const res = await useMyFetch(`/organizations/${organizationId}/orphaned-content`)
      return (res.data.value as OrphanedOwner[]) || []
    } catch {
      return []
    }
  }

  const transferMemberContent = async (
    organizationId: string,
    membershipId: string,
    toUserId: string,
  ): Promise<TransferResult | null> => {
    saving.value = true
    error.value = ''
    try {
      const res = await useMyFetch(
        `/organizations/${organizationId}/members/${membershipId}/transfer-content`,
        { method: 'POST', body: { to_user_id: toUserId, include_projects: true } },
      )
      return (res.data.value as TransferResult) || null
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not transfer'
      return null
    } finally {
      saving.value = false
    }
  }

  // ── the copy you can keep ────────────────────────────────────────────────
  // The layer under every transfer above: those move ownership INSIDE this
  // install, this produces a file that survives the install being gone.
  //
  // ★`responseType: 'blob'` is not optional. Without it the zip is decoded as
  // text, and what downloads is a corrupt archive that opens nowhere — a
  // failure nobody sees until the day they actually need the backup.
  const downloadBundle = async (
    path: string,
    fallbackName: string,
  ): Promise<boolean> => {
    saving.value = true
    error.value = ''
    try {
      const { data, error: fetchError } = await useMyFetch(path, { responseType: 'blob' })
      if (fetchError.value || !data.value) {
        error.value = 'Could not build the export'
        return false
      }
      const url = window.URL.createObjectURL(data.value as Blob)
      const link = document.createElement('a')
      link.style.display = 'none'
      link.href = url
      // The server already sends a Content-Disposition filename; this is only
      // the fallback for browsers that ignore it on a blob URL.
      link.download = fallbackName
      document.body.appendChild(link)
      link.click()
      window.URL.revokeObjectURL(url)
      link.remove()
      return true
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not build the export'
      return false
    } finally {
      saving.value = false
    }
  }

  const exportMyContent = () =>
    downloadBundle('/api/me/content/export', 'my-content-export.zip')

  const exportMemberContent = (organizationId: string, membershipId: string) =>
    downloadBundle(
      `/organizations/${organizationId}/members/${membershipId}/content-export`,
      'content-export.zip',
    )

  const undo = async (batchId?: string) => {
    const id = batchId || lastBatchId.value
    if (!id) return false
    saving.value = true
    error.value = ''
    try {
      await useMyFetch(`/api/me/transfers/${id}/undo`, { method: 'POST' })
      if (id === lastBatchId.value) lastBatchId.value = null
      await fetchContent()
      return true
    } catch (e: any) {
      error.value = e?.data?.detail || e?.message || 'Could not undo'
      return false
    } finally {
      saving.value = false
    }
  }

  return {
    summary,
    items,
    successor,
    loading,
    saving,
    error,
    lastBatchId,
    fetchContent,
    fetchSuccessor,
    saveSuccessor,
    handOver,
    handOverReport,
    fetchMemberSummary,
    fetchOrphans,
    exportMyContent,
    exportMemberContent,
    transferMemberContent,
    undo,
  }
}
