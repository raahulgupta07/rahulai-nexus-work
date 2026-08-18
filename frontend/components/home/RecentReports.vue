<template>
  <div v-if="!isLoading && hasAnyReports" class="mt-12">
    <div data-testid="recent-header" class="flex items-center justify-between mb-4">
      <USelectMenu
        v-model="viewMode"
        :options="availableOptions"
        value-attribute="value"
        option-attribute="label"
        size="md"
        :ui="{
          trigger: 'ring-0 shadow-none bg-transparent hover:bg-gray-50 dark:hover:bg-gray-800 font-medium text-gray-900 dark:text-white',
          width: 'w-72'
        }"
      >
        <template #default>
          <span class="text-base font-medium text-gray-900 dark:text-white">{{ selectedLabel }}</span>
          <UIcon name="i-heroicons-chevron-down-20-solid" class="w-5 h-5 text-gray-400 ms-1" />
        </template>
      </USelectMenu>
      <NuxtLink to="/reports" class="text-sm text-blue-600 hover:text-blue-800 hover:underline">
        View All Reports
      </NuxtLink>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      <RecentReportCard
        v-for="report in displayedReports"
        :key="report.id"
        :report="report"
        :view-mode="viewMode"
        :is-owner="report.user?.id === (currentUser as any)?.id"
      />
    </div>
  </div>

  <!-- Loading state -->
  <div v-else-if="isLoading" class="mt-12">
    <!-- ★40px, not 20px. The real row is a USelectMenu size md — @nuxt/ui gives
         that px-3 py-2 (16px) around a text-base slot (24px line box) — beside a
         text-sm link. The old single h-5 bar was half the height and dropped the
         link entirely, so the whole grid stepped down 20px on settle. -->
    <div data-testid="recent-header" class="flex items-center justify-between mb-4">
      <div class="h-10 flex items-center">
        <div class="h-4 w-40 bg-gray-200 dark:bg-gray-700 rounded animate-pulse"></div>
      </div>
      <div class="h-5 flex items-center">
        <div class="h-3.5 w-28 bg-gray-200 dark:bg-gray-700 rounded animate-pulse"></div>
      </div>
    </div>
    <!-- ★The placeholder mirrors RecentReportCard, it does not approximate it.
         It used to render 4 cards against a list that slices to 8, so the grid
         grew a whole row on settle; and its body was 60px against the card's
         64px (p-3 + text-sm/20px + mt-1 + text-xs/16px + p-3), so every row
         nudged again. Card count, ground, border and the two text bands are
         all read off RecentReportCard.vue — change one, change both. -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      <div
        v-for="i in 8"
        :key="i"
        data-testid="report-card-bone"
        class="bg-white dark:bg-gray-900 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden"
      >
        <div class="aspect-[4/3] bg-gray-100 dark:bg-gray-800 animate-pulse"></div>
        <div class="p-3">
          <!-- h-5 is the card title's text-sm line box; h-4 mt-1 is the byline's. -->
          <div class="h-5 flex items-center">
            <div class="h-3.5 w-3/4 bg-gray-200 dark:bg-gray-700 rounded animate-pulse"></div>
          </div>
          <div class="h-4 mt-1 flex items-center">
            <div class="h-2.5 w-1/2 bg-gray-200 dark:bg-gray-700 rounded animate-pulse"></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import RecentReportCard from './RecentReportCard.vue'

interface RecentReport {
  id: string
  title?: string
  slug: string
  status: string
  user: { id: string; name?: string; email?: string }
  artifact_modes: string[]
  conversation_share_enabled: boolean
  conversation_share_token?: string
  created_at: string
}

const { data: currentUser } = useAuth()
const { organization } = useOrganization()
const { fetchActivity } = useReportActivity()

const orgReports = ref<RecentReport[]>([])
const myReports = ref<RecentReport[]>([])
const isLoading = ref(true)
const viewMode = ref('org')

const orgName = computed(() => organization.value?.name || 'Organization')

const hasAnyReports = computed(() => {
  return orgReports.value.length > 0 || myReports.value.length > 0
})

// Build available options based on what's available
const availableOptions = computed(() => {
  const options = []
  if (orgReports.value.length > 0) {
    options.push({ label: `${orgName.value} Reports`, value: 'org' })
  }
  if (myReports.value.length > 0) {
    options.push({ label: 'My Reports', value: 'my' })
  }
  return options
})

const selectedLabel = computed(() => {
  if (viewMode.value === 'org') return `${orgName.value} Reports`
  return 'My Reports'
})

const displayedReports = computed(() => {
  const list = viewMode.value === 'org' ? orgReports.value : myReports.value
  return list.slice(0, 8)
})

// Auto-select valid mode when data changes
watch([orgReports, myReports], () => {
  if (viewMode.value === 'org' && orgReports.value.length === 0 && myReports.value.length > 0) {
    viewMode.value = 'my'
  } else if (viewMode.value === 'my' && myReports.value.length === 0 && orgReports.value.length > 0) {
    viewMode.value = 'org'
  }
})

const fetchReports = async () => {
  try {
    // Fetch org (published) reports and my reports in parallel
    const [orgResponse, myResponse] = await Promise.all([
      useMyFetch('/reports', {
        method: 'GET',
        query: { filter: 'published', limit: 8 }
      }),
      useMyFetch('/reports', {
        method: 'GET',
        query: { filter: 'my', limit: 8 }
      })
    ])

    if (!orgResponse.error.value && orgResponse.data.value) {
      orgReports.value = (orgResponse.data.value as any).reports || []
    }
    if (!myResponse.error.value && myResponse.data.value) {
      myReports.value = (myResponse.data.value as any).reports || []
    }
    fetchActivity([...orgReports.value, ...myReports.value].map((r: any) => r.id))
  } catch (e) {
    console.error('Failed to fetch recent reports:', e)
    orgReports.value = []
    myReports.value = []
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  fetchReports()
})
</script>
