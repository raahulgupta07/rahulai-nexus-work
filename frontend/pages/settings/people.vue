<template>
  <div class="mt-4 space-y-4">

    <!-- Header -->
    <div>
      <h1 class="text-sm font-medium text-gray-900 dark:text-white">{{ t('settings.people.title') }}</h1>
      <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 max-w-2xl">{{ t('settings.people.subtitle') }}</p>
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
    <p class="text-[11px] text-gray-400 dark:text-gray-500 -mt-2 flex items-center gap-2 flex-wrap">
      <span>{{ t('settings.people.count', { n: filtered.length }) }}</span>
      <span>·</span>
      <span>{{ t('settings.people.clickHint') }}</span>
      <span>·</span>
      <code class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono text-[10px]">{{ t('settings.people.mergeKey') }}</code>
    </p>

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

    <!-- People list -->
    <div v-else class="space-y-2">
      <div
        v-for="person in filtered"
        :key="person.user_id"
        class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
      >
        <!-- Card header -->
        <button
          class="w-full flex items-center gap-3 px-3 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
          @click="toggle(person.user_id)"
        >
          <!-- Avatar -->
          <span
            class="w-9 h-9 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0"
            :style="{ background: avatarColor(person) }"
          >{{ initials(person.name || person.email) }}</span>

          <!-- Name + email -->
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-xs font-semibold text-gray-800 dark:text-gray-100 truncate">{{ person.name || person.email }}</span>
              <span
                class="px-1.5 py-0.5 rounded-full text-[10px] font-medium flex-shrink-0"
                :class="person.is_owner
                  ? 'bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-400'
                  : 'bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400'"
              >{{ person.role }}</span>
            </div>
            <div class="font-mono text-[11px] text-gray-400 dark:text-gray-500 truncate">{{ person.email }}</div>
          </div>

          <!-- Source badges -->
          <div class="flex items-center gap-1 flex-shrink-0 flex-wrap justify-end max-w-[210px]">
            <span
              v-for="src in sourceRow(person)"
              :key="src.key"
              class="w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold text-white transition-opacity"
              :class="src.has ? '' : 'opacity-25 grayscale'"
              :style="{ background: src.color }"
              :title="src.has ? src.label : `${src.label} (${t('settings.people.notLinked')})`"
            >{{ src.badge }}</span>
          </div>

          <!-- Chevron -->
          <svg
            class="w-4 h-4 text-gray-400 transition-transform flex-shrink-0"
            :class="isOpen(person.user_id) ? 'rotate-180' : ''"
            fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"
          ><path stroke-linecap="round" stroke-linejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" /></svg>
        </button>

        <!-- Card body -->
        <div v-show="isOpen(person.user_id)" class="px-3 pb-3 border-t border-gray-100 dark:border-gray-800 pt-3">
          <!-- Merge line -->
          <p class="text-[11px] text-gray-400 dark:text-gray-500 mb-3 flex items-center gap-1.5 flex-wrap">
            <svg class="w-3 h-3 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" d="M12 6v12m6-6H6" /></svg>
            <span>{{ t('settings.people.mergedSummary', { email: person.email.toLowerCase(), n: person.identities.length }) }}</span>
            <template v-if="person.created_at">
              <span>·</span>
              <span>{{ t('settings.people.joined', { when: formatDate(person.created_at) }) }}</span>
            </template>
          </p>

          <!-- Identities -->
          <div class="space-y-0">
            <div
              v-for="(idn, idx) in person.identities"
              :key="idx"
              class="flex items-center gap-3 py-2.5"
              :class="{ 'border-t border-gray-100 dark:border-gray-800': idx > 0 }"
            >
              <span
                class="w-7 h-7 rounded flex items-center justify-center text-white text-[11px] font-bold flex-shrink-0"
                :style="{ background: providerMeta(idn.provider).color }"
              >{{ providerMeta(idn.provider).badge }}</span>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">{{ providerMeta(idn.provider).label }}</span>
                  <span
                    class="px-1.5 py-0.5 rounded text-[9px] font-semibold tracking-wide"
                    :class="tagClass(idnTag(person, idn))"
                  >{{ t(`settings.people.tag.${idnTag(person, idn)}`) }}</span>
                </div>
                <div class="font-mono text-[11px] text-gray-400 dark:text-gray-500 truncate">{{ idnDetail(person, idn) }}</div>
              </div>
            </div>
          </div>

          <!-- Group memberships -->
          <div v-if="person.groups && person.groups.length" class="mt-3 pt-3 border-t border-dashed border-gray-200 dark:border-gray-700">
            <p class="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-2">{{ t('settings.people.groupMemberships') }}</p>
            <div class="flex flex-wrap gap-1.5">
              <span
                v-for="(g, gi) in person.groups"
                :key="gi"
                class="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300"
              >
                {{ g.name }}
                <span v-if="g.source" class="font-mono text-[9px] text-gray-400 dark:text-gray-500">{{ t('settings.people.via', { source: g.source }) }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Legend -->
    <div v-if="!loading && !error && people.length" class="flex flex-wrap items-center gap-4 pt-2 text-[11px] text-gray-400 dark:text-gray-500">
      <span v-for="src in legend" :key="src.key" class="flex items-center gap-1.5">
        <span class="w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold text-white" :style="{ background: src.color }">{{ src.badge }}</span>
        {{ src.label }}
      </span>
    </div>

  </div>
</template>

<script setup lang="ts">
import type { Person, PersonIdentity } from '~/composables/usePeople'

definePageMeta({
  auth: true,
  permissions: ['view_members'],
  layout: 'settings',
})

const { t } = useI18n()
const { people, loading, error, fetchPeople } = usePeople()

const search = ref('')
const openIds = ref<Set<string>>(new Set())

// ── Provider presentation ──────────────────────────────────────────────
// provider slug → { label, short badge, color }. Directory sources
// (ldap / azure_ad / okta / scim) all collapse onto one "Directory" badge.
type ProviderMeta = { label: string; badge: string; color: string }
const PROVIDERS: Record<string, ProviderMeta> = {
  local: { label: t('settings.people.provider.local'), badge: '🔑', color: '#64748b' },
  keycloak: { label: t('settings.people.provider.keycloak'), badge: 'K', color: '#3b4cca' },
  google: { label: t('settings.people.provider.google'), badge: 'G', color: '#ea4335' },
  entra: { label: t('settings.people.provider.entra'), badge: '⊞', color: '#0067b8' },
  directory: { label: t('settings.people.provider.directory'), badge: 'D', color: '#0b7285' },
}
const DIRECTORY_SOURCES = ['ldap', 'azure_ad', 'okta', 'scim']

const providerMeta = (provider: string): ProviderMeta => {
  if (PROVIDERS[provider]) return PROVIDERS[provider]
  if (DIRECTORY_SOURCES.includes(provider)) return PROVIDERS.directory
  // Unknown OAuth provider — render a generic slate chip with its initial.
  return { label: provider.charAt(0).toUpperCase() + provider.slice(1), badge: (provider[0] || '?').toUpperCase(), color: '#475569' }
}

// Ordered source badge row shown on every card header.
const SOURCE_ORDER = ['local', 'keycloak', 'google', 'entra', 'directory']
const sourceRow = (person: Person) => {
  const linked = new Set<string>()
  for (const idn of person.identities) {
    linked.add(DIRECTORY_SOURCES.includes(idn.provider) ? 'directory' : idn.provider)
  }
  // A directory group membership also means the person is directory-backed.
  if ((person.groups || []).some((g) => g.source && DIRECTORY_SOURCES.includes(g.source))) linked.add('directory')
  return SOURCE_ORDER.map((key) => ({
    key,
    has: linked.has(key),
    label: PROVIDERS[key].label,
    badge: PROVIDERS[key].badge,
    color: PROVIDERS[key].color,
  }))
}

const legend = computed(() => SOURCE_ORDER.map((key) => ({ key, ...PROVIDERS[key] })))

// ── Identity tag (PRIMARY / LINKED) ────────────────────────────────────
const idnTag = (person: Person, idn: PersonIdentity): string => {
  if (idn.is_primary) return 'primary'
  return 'linked'
}
const tagClass = (tag: string): string => {
  switch (tag) {
    case 'primary': return 'bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400'
    case 'verified': return 'bg-blue-50 dark:bg-blue-950/60 text-blue-600 dark:text-blue-400'
    case 'unverified': return 'bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-400'
    default: return 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'
  }
}

// Mono detail line under each identity.
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

// ── Expand / collapse ──────────────────────────────────────────────────
const isOpen = (id: string) => openIds.value.has(id)
const toggle = (id: string) => {
  const next = new Set(openIds.value)
  next.has(id) ? next.delete(id) : next.add(id)
  openIds.value = next
}

// ── Client-side search (name / email / provider / group / role) ─────────
const filtered = computed<Person[]>(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return people.value
  return people.value.filter((p) => {
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

onMounted(async () => {
  await fetchPeople()
  // Open the first card by default so the layout reads at a glance.
  if (people.value.length) openIds.value = new Set([people.value[0].user_id])
})
</script>
