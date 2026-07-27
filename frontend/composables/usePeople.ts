// People & Identities composable
// Fetches the merged people list (one account per email, with all linked
// identities + directory group memberships) for the current organization.

export type PersonIdentity = {
  kind: 'local' | 'oauth'
  provider: 'local' | 'keycloak' | 'google' | 'entra' | string
  account_email: string | null
  account_id: string | null
  is_primary: boolean
}

export type PersonGroup = {
  name: string
  source: 'azure_ad' | 'okta' | 'scim' | 'manual' | 'ldap' | null
}

export type Person = {
  user_id: string
  email: string
  name: string
  role: string
  is_owner: boolean
  created_at: string | null
  has_password: boolean
  identities: PersonIdentity[]
  groups: PersonGroup[]
}

export const usePeople = () => {
  const { organization } = useOrganization()
  const people = ref<Person[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const fetchPeople = async (): Promise<Person[]> => {
    loading.value = true
    error.value = null
    try {
      const orgId = organization.value.id
      if (!orgId) throw new Error('No organization selected')
      const res = await useMyFetch(`/api/organizations/${orgId}/people`)
      if (res.status.value !== 'success') {
        const msg = (res.error?.value as any)?.data?.detail || 'Failed to fetch people'
        throw new Error(msg)
      }
      people.value = (res.data.value as Person[]) || []
      return people.value
    } catch (e: any) {
      error.value = e.message || 'Failed to fetch people'
      people.value = []
      return []
    } finally {
      loading.value = false
    }
  }

  return { people, loading, error, fetchPeople }
}
