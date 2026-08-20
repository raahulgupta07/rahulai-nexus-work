<template>
  <!-- ★On a wide screen the drawer is fixed to the right edge and would sit ON TOP of
       the table. It is NON-MODAL by design — the reader must be able to compare the
       open person against the rest of the list — so the page reserves the width
       instead of dimming what is underneath. Below lg the drawer is a bottom sheet
       and no reservation is needed. -->
  <div class="mt-4 space-y-4 transition-[padding]" :class="selectedPerson ? 'lg:pe-[456px]' : ''">

    <!-- Header -->
    <div>
      <h1 class="text-sm font-medium text-gray-900 dark:text-white">{{ t('settings.people.title') }}</h1>
      <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 max-w-2xl">{{ t('settings.people.subtitle') }}</p>
    </div>

    <!-- Summary — derived from the rows already loaded, no second request. -->
    <div v-if="!loading && !error && people.length" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
      <div
        v-for="stat in summaryTiles"
        :key="stat.key"
        class="border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2"
      >
        <div class="text-lg font-semibold text-gray-900 dark:text-gray-100 leading-tight tabular-nums">{{ stat.value }}</div>
        <div class="text-[11px] text-gray-500 dark:text-gray-400 leading-tight mt-0.5">{{ stat.label }}</div>
      </div>
    </div>

    <!-- Search -->
    <div class="flex items-center gap-2 border border-gray-200 dark:border-gray-700 rounded px-3 py-2">
      <svg class="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
        <circle cx="11" cy="11" r="7" /><path stroke-linecap="round" d="m21 21-4.3-4.3" />
      </svg>
      <input
        v-model="search"
        type="text"
        :placeholder="t('settings.people.searchPlaceholder')"
        class="w-full bg-transparent border-0 p-0 text-xs text-gray-800 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-0"
      />
    </div>

    <!-- Filters. Client-side, one at a time, and they compose with the search box:
         "who can still sign in with a password?" is the question this screen exists
         to answer, and it has to be answerable in one click. -->
    <div class="flex items-center gap-1.5 flex-wrap">
      <button
        v-for="f in FILTERS"
        :key="f"
        type="button"
        :aria-pressed="activeFilter === f"
        class="px-2.5 py-1 rounded-full text-[11px] font-medium border transition-colors"
        :class="activeFilter === f
          ? 'bg-gray-900 text-white border-gray-900 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-100'
          : 'bg-transparent text-gray-600 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/60'"
        @click="setFilter(f)"
      >{{ t(`settings.people.filter.${f}`) }}</button>
      <span class="text-[11px] text-gray-400 dark:text-gray-500 ms-1">{{ t('settings.people.count', { n: filtered.length }) }}</span>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="py-12 text-center">
      <div class="inline-block w-4 h-4 border-2 border-gray-200 dark:border-gray-700 border-t-gray-500 rounded-full animate-spin"></div>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="py-6 text-center text-xs text-red-500">{{ error }}</div>

    <!-- Empty -->
    <div v-else-if="!filtered.length" class="py-12 text-center text-xs text-gray-400 dark:text-gray-500">
      {{ people.length ? t('settings.people.noMatch') : t('settings.people.empty') }}
    </div>

    <!-- People table. ★The scroll container is here, not on the page: a narrow
         viewport scrolls the TABLE sideways, never the body. -->
    <div v-else class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-x-auto">
      <table class="w-full min-w-[640px] text-left">
        <thead>
          <tr class="border-b border-gray-200 dark:border-gray-700">
            <th scope="col" class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ t('settings.people.col.person') }}</th>
            <th scope="col" class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ t('settings.people.col.methods') }}</th>
            <th scope="col" class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ t('settings.people.col.role') }}</th>
            <th scope="col" class="px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">{{ t('settings.people.col.joined') }}</th>
            <th scope="col" class="w-8 px-2 py-2"><span class="sr-only">{{ t('settings.people.panel.open') }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="person in filtered"
            :key="person.user_id"
            class="border-t border-gray-100 dark:border-gray-800 transition-colors"
            :class="[
              selectedId === person.user_id ? 'bg-blue-50 dark:bg-blue-950/40' : '',
              hasDetail(person) ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50' : '',
            ]"
            @click="hasDetail(person) && openPerson(person.user_id)"
          >
            <!-- Person -->
            <td class="px-3 py-2.5 align-middle">
              <div class="flex items-center gap-2.5 min-w-0">
                <span
                  class="w-8 h-8 rounded-full flex items-center justify-center text-white text-[11px] font-bold flex-shrink-0"
                  :style="{ background: avatarColor(person) }"
                >{{ initials(person.name || person.email) }}</span>
                <div class="min-w-0">
                  <!-- ★Four of eight people on a real org have no display name. Printing
                       the email as the bold title AND as the subtitle showed the same
                       string twice, so the subtitle only exists when there is a name. -->
                  <div class="text-xs font-semibold text-gray-800 dark:text-gray-100 truncate">{{ person.name || person.email }}</div>
                  <div v-if="person.name" class="font-mono text-[11px] text-gray-400 dark:text-gray-500 truncate">{{ person.email }}</div>
                </div>
              </div>
            </td>

            <!-- How they sign in — named chips, only for methods the person HAS. -->
            <td class="px-3 py-2.5 align-middle">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span
                  v-for="(m, mi) in signInMethods(person)"
                  :key="mi"
                  class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium whitespace-nowrap"
                  :class="methodClass(m.kind)"
                >
                  {{ m.label }}
                  <span v-if="m.primary" class="text-[9px] font-semibold tracking-wide opacity-70">{{ t('settings.people.tag.primary') }}</span>
                </span>
                <span v-if="!signInMethods(person).length" class="text-[11px] text-gray-400 dark:text-gray-500">—</span>
              </div>
            </td>

            <!-- Role. Deliberately NEUTRAL: amber / indigo / teal are spoken for by
                 the sign-in chips, and a second meaning for a colour un-teaches the
                 first one. -->
            <td class="px-3 py-2.5 align-middle">
              <span
                class="px-1.5 py-0.5 rounded-full text-[10px] font-medium whitespace-nowrap"
                :class="person.is_owner
                  ? 'bg-gray-800 text-white dark:bg-gray-200 dark:text-gray-900'
                  : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'"
              >{{ person.role }}</span>
            </td>

            <!-- Joined -->
            <td class="px-3 py-2.5 align-middle whitespace-nowrap text-[11px] text-gray-500 dark:text-gray-400">
              {{ person.created_at ? formatDate(person.created_at) : '—' }}
            </td>

            <!-- Disclosure. ★Only where there is something behind it — see hasDetail:
                 the best expand is the one you do not need. -->
            <td class="w-8 px-2 py-2.5 align-middle text-right">
              <button
                v-if="hasDetail(person)"
                type="button"
                :aria-label="t('settings.people.panel.open')"
                :aria-expanded="selectedId === person.user_id"
                class="p-1 rounded text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
                @click.stop="openPerson(person.user_id)"
              >
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                </svg>
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Footnote. The merge rule is worth stating once, quietly, at the foot —
         not as a code chip above the list where it read like a setting. -->
    <p v-if="!loading && !error && people.length" class="text-[11px] text-gray-400 dark:text-gray-500">
      {{ t('settings.people.mergeFootnote') }}
    </p>

    <!-- ────────────────────────────────────────────────────────────────────
         Detail drawer. A plain <aside>, deliberately not one of the Nuxt UI
         overlay components: this is inspection, not a decision, so there is no
         backdrop, no dimming and no focus trap — the list stays readable AND
         clickable, and clicking a different person swaps the content in place.
         The popover component is additionally ruled out because it clips
         absolutely-positioned children, which this codebase has been bitten by.
         Bottom sheet below lg, right rail from lg up.
         ──────────────────────────────────────────────────────────────────── -->
    <aside
      v-if="selectedPerson"
      :aria-label="t('settings.people.panel.open')"
      class="fixed z-30 bg-white dark:bg-gray-900 shadow-2xl overflow-y-auto border-gray-200 dark:border-gray-700
             inset-x-0 bottom-0 max-h-[70vh] border-t rounded-t-xl
             lg:inset-x-auto lg:inset-y-0 lg:end-0 lg:w-[440px] lg:max-h-none lg:rounded-none lg:border-t-0 lg:border-s"
    >
      <div class="sticky top-0 bg-white dark:bg-gray-900 border-b border-gray-100 dark:border-gray-800 px-4 py-3 flex items-start gap-3">
        <span
          class="w-9 h-9 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
          :style="{ background: avatarColor(selectedPerson) }"
        >{{ initials(selectedPerson.name || selectedPerson.email) }}</span>
        <div class="flex-1 min-w-0">
          <div class="text-xs font-semibold text-gray-800 dark:text-gray-100 truncate">{{ selectedPerson.name || selectedPerson.email }}</div>
          <div class="font-mono text-[11px] text-gray-400 dark:text-gray-500 truncate">{{ selectedPerson.email }}</div>
        </div>
        <button
          type="button"
          :aria-label="t('settings.people.panel.close')"
          class="p-1 -me-1 rounded text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 flex-shrink-0"
          @click="closePanel"
        >
          <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="px-4 py-3 space-y-4">
        <p class="text-[11px] text-gray-500 dark:text-gray-400">
          {{ selectedPerson.identities.length === 1
            ? t('settings.people.panel.oneIdentity')
            : t('settings.people.panel.identities', { n: selectedPerson.identities.length }) }}
        </p>

        <!-- One block per identity -->
        <div
          v-for="(idn, idx) in selectedPerson.identities"
          :key="idx"
          class="border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2.5"
        >
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">{{ providerMeta(idn.provider).label }}</span>
            <span
              class="px-1.5 py-0.5 rounded text-[9px] font-semibold tracking-wide"
              :class="tagClass(idnTag(idn))"
            >{{ t(`settings.people.tag.${idnTag(idn)}`) }}</span>
          </div>
          <!-- ★A directory DN is long. Its own horizontal scroller, so it is never
               clipped and never widens the page. -->
          <div class="mt-1 overflow-x-auto">
            <div class="font-mono text-[11px] text-gray-500 dark:text-gray-400 whitespace-nowrap">{{ idnDetail(selectedPerson, idn) }}</div>
          </div>
        </div>

        <!-- Group memberships -->
        <div v-if="selectedPerson.groups && selectedPerson.groups.length">
          <p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ t('settings.people.groupMemberships') }}</p>
          <div class="flex flex-wrap gap-1.5">
            <span
              v-for="(g, gi) in selectedPerson.groups"
              :key="gi"
              class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300"
            >
              {{ g.name }}
              <span v-if="g.source" class="font-mono text-[9px] text-gray-400 dark:text-gray-500">{{ t('settings.people.via', { source: g.source }) }}</span>
            </span>
          </div>
        </div>

        <p v-if="selectedPerson.created_at" class="text-[11px] text-gray-400 dark:text-gray-500">
          {{ t('settings.people.joined', { when: formatDate(selectedPerson.created_at) }) }}
        </p>

        <p v-if="selectedPerson.identities.length >= 2" class="text-[11px] text-gray-400 dark:text-gray-500 pt-1 border-t border-dashed border-gray-200 dark:border-gray-700">
          {{ t('settings.people.panel.sameEmail') }}
        </p>
      </div>
    </aside>

  </div>
</template>

<script setup lang="ts">
import type { Person, PersonIdentity } from '~/composables/usePeople'

definePageMeta({
  auth: true,
  // ★An ADMINISTRATION screen: it lists every person in the organization with
  // their email, role, linked identity providers and join date. `view_members`
  // is a baseline permission every member holds (it backs sharing pickers), so
  // gating this page on it hands the whole staff directory to any member — the
  // settings nav already gates the tab on `manage_settings` for exactly that
  // reason (see layouts/settings.vue), and so does the backend endpoint. Do not
  // weaken this.
  permissions: ['manage_settings'],
  layout: 'settings',
})

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { people, loading, error, fetchPeople } = usePeople()

const search = ref('')

// ── Provider presentation ──────────────────────────────────────────────
// provider slug → display label. Directory sources (ldap / azure_ad / okta /
// scim) all collapse onto one "Directory" label.
type ProviderMeta = { label: string }
const PROVIDERS: Record<string, ProviderMeta> = {
  local: { label: t('settings.people.provider.local') },
  keycloak: { label: t('settings.people.provider.keycloak') },
  google: { label: t('settings.people.provider.google') },
  entra: { label: t('settings.people.provider.entra') },
  directory: { label: t('settings.people.provider.directory') },
}
const DIRECTORY_SOURCES = ['ldap', 'azure_ad', 'okta', 'scim']

const providerMeta = (provider: string): ProviderMeta => {
  if (PROVIDERS[provider]) return PROVIDERS[provider]
  if (DIRECTORY_SOURCES.includes(provider)) return PROVIDERS.directory
  // Unknown OAuth provider — title-case its own slug rather than inventing a name.
  return { label: provider.charAt(0).toUpperCase() + provider.slice(1) }
}

// ── Sign-in methods ────────────────────────────────────────────────────
// Three semantic kinds, one colour each. ★Only methods the person HAS are ever
// rendered: the old row drew all five providers with the absent ones dimmed,
// which on a real org of eight people meant 32 badges saying "no" against 8
// saying "yes" — two of them for providers the org has never configured.
type MethodKind = 'password' | 'sso' | 'directory'
type SignInMethod = { kind: MethodKind; label: string; primary: boolean }

const identityKind = (idn: PersonIdentity): MethodKind => {
  if (idn.provider === 'local') return 'password'
  if (idn.kind === 'directory' || DIRECTORY_SOURCES.includes(idn.provider)) return 'directory'
  return 'sso'
}

const signInMethods = (person: Person): SignInMethod[] => {
  const out: SignInMethod[] = []
  for (const idn of person.identities) {
    const kind = identityKind(idn)
    // ★A `local` identity with no password is not a way in. "Can this person
    // still sign in with a password?" is the question the page exists for, and
    // `has_password` is the only field that answers it.
    if (kind === 'password' && !person.has_password) continue
    out.push({
      kind,
      label: kind === 'password' ? t('settings.people.method.password') : providerMeta(idn.provider).label,
      primary: !!idn.is_primary,
    })
  }
  // A password with no matching `local` row still counts — the row records what
  // the account was created FROM, the flag records what it can be used WITH.
  if (person.has_password && !out.some((m) => m.kind === 'password')) {
    out.unshift({ kind: 'password', label: t('settings.people.method.password'), primary: false })
  }
  return out
}

const methodClass = (kind: MethodKind): string => {
  switch (kind) {
    case 'password': return 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-500/10 dark:text-amber-400 dark:border-amber-500/30'
    case 'sso': return 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-400 dark:border-indigo-500/30'
    default: return 'bg-teal-50 text-teal-700 border-teal-200 dark:bg-teal-500/10 dark:text-teal-400 dark:border-teal-500/30'
  }
}

const hasSso = (p: Person) => p.identities.some((i) => identityKind(i) === 'sso')
const hasDirectory = (p: Person) => p.identities.some((i) => identityKind(i) === 'directory')
const isAdmin = (p: Person) => p.is_owner || (p.role || '').toLowerCase().includes('admin')

// ── Summary ────────────────────────────────────────────────────────────
// Derived from the rows already loaded — no second request.
const summary = computed(() => {
  const rows = people.value
  return {
    people: rows.length,
    password: rows.filter((p) => p.has_password).length,
    sso: rows.filter(hasSso).length,
    directory: rows.filter(hasDirectory).length,
    admins: rows.filter(isAdmin).length,
  }
})

const summaryTiles = computed(() => [
  { key: 'people', value: summary.value.people, label: t('settings.people.summaryPeople') },
  { key: 'password', value: summary.value.password, label: t('settings.people.summaryPassword') },
  { key: 'sso', value: summary.value.sso, label: t('settings.people.summarySso') },
  { key: 'directory', value: summary.value.directory, label: t('settings.people.summaryDirectory') },
  { key: 'admins', value: summary.value.admins, label: t('settings.people.summaryAdmins') },
])

// ── Filters ────────────────────────────────────────────────────────────
const FILTERS = ['all', 'password', 'sso', 'directory', 'multiple', 'admins'] as const
type Filter = typeof FILTERS[number]
const activeFilter = ref<Filter>('all')
// Clicking the active pill returns to "all" — otherwise the only way out of a
// filter is to hunt for the pill that means "no filter".
const setFilter = (f: Filter) => { activeFilter.value = activeFilter.value === f ? 'all' : f }

const matchesFilter = (p: Person): boolean => {
  switch (activeFilter.value) {
    case 'password': return p.has_password
    case 'sso': return hasSso(p)
    case 'directory': return hasDirectory(p)
    case 'multiple': return p.identities.length > 1
    case 'admins': return isAdmin(p)
    default: return true
  }
}

// ── Identity tag (PRIMARY / LINKED) ────────────────────────────────────
const idnTag = (idn: PersonIdentity): string => (idn.is_primary ? 'primary' : 'linked')
const tagClass = (tag: string): string => {
  switch (tag) {
    case 'primary': return 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400'
    case 'verified': return 'bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400'
    case 'unverified': return 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400'
    default: return 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
  }
}

// Mono detail line under each identity in the panel.
const idnDetail = (person: Person, idn: PersonIdentity): string => {
  if (idn.provider === 'local') {
    return person.has_password
      ? t('settings.people.detail.passwordSet')
      : t('settings.people.detail.noPassword')
  }
  const parts: string[] = []
  if (idn.account_email) parts.push(idn.account_email)
  if (idn.account_id) parts.push(`id: ${idn.account_id}`)
  return parts.length ? parts.join(' · ') : t('settings.people.detail.linkedAccount')
}

// ── Avatar helpers ─────────────────────────────────────────────────────
const AVATAR_COLORS = ['#2563eb', '#16a34a', '#b45309', '#7c3aed', '#db2777', '#0891b2', '#dc2626']
const initials = (s: string): string =>
  (s || '?').trim().split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase()
const avatarColor = (person: Person): string => {
  const key = person.email || person.user_id || ''
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return AVATAR_COLORS[h % AVATAR_COLORS.length]
}

const formatDate = (iso: string): string => {
  try {
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  } catch { return iso }
}

// ── Detail panel ───────────────────────────────────────────────────────
// ★Only offer the disclosure where there is something behind it. One identity
// and no groups means the panel repeats what the row already says — roughly
// five of eight rows on a real org. The best expand is the one you do not need.
const hasDetail = (person: Person): boolean =>
  person.identities.length > 1 || (person.groups || []).length > 0

// ★Selection lives in the URL, like the open run on the Keeper screen: a link to
// a person is then shareable and the back button undoes the selection.
const selectedId = computed<string | null>(() => (route.query.person as string) || null)
// Resolved against the WHOLE list, not the filtered one, so a pasted link opens
// its person even when the current filter would hide the row.
const selectedPerson = computed<Person | null>(() =>
  people.value.find((p) => p.user_id === selectedId.value) || null)

function setQuery(patch: Record<string, string | undefined>) {
  const query: Record<string, any> = { ...route.query, ...patch }
  Object.keys(query).forEach((k) => { if (query[k] === undefined) delete query[k] })
  router.push({ query })
}

const openPerson = (id: string) => setQuery({ person: id })
const closePanel = () => setQuery({ person: undefined })

// Escape closes it. No focus trap: the drawer is non-modal by design and the
// list behind it must stay operable.
const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Escape' && selectedId.value) closePanel()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

// ── Client-side search (name / email / provider / group / role) ─────────
const filtered = computed<Person[]>(() => {
  const q = search.value.trim().toLowerCase()
  return people.value.filter((p) => {
    if (!matchesFilter(p)) return false
    if (!q) return true
    if ((p.name || '').toLowerCase().includes(q)) return true
    if ((p.email || '').toLowerCase().includes(q)) return true
    if ((p.role || '').toLowerCase().includes(q)) return true
    if (p.identities.some((i) => i.provider.toLowerCase().includes(q)
      || providerMeta(i.provider).label.toLowerCase().includes(q)
      || (i.account_email || '').toLowerCase().includes(q))) return true
    if ((p.groups || []).some((g) => g.name.toLowerCase().includes(q)
      || (g.source || '').toLowerCase().includes(q))) return true
    return false
  })
})

onMounted(fetchPeople)
</script>
