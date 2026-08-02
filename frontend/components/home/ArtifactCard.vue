<template>
  <NuxtLink :to="link" class="group block rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden bg-white dark:bg-gray-900 hover:border-gray-300 dark:hover:border-gray-700 transition-colors">
    <div class="aspect-[4/3] relative flex items-center justify-center" :class="style.cardBg">
      <img
        v-if="thumbnailUrl && !imageError"
        :src="thumbnailUrl"
        :alt="title"
        class="absolute inset-0 w-full h-full object-cover object-top"
        @error="imageError = true"
      />
      <UIcon v-else :name="icon" class="w-10 h-10" :class="style.iconColor" />
      <span class="absolute start-2 bottom-2 text-[10px] font-semibold px-2 py-0.5 rounded" :class="[style.bg, style.text]">{{ style.label }}</span>
    </div>
    <div class="p-3">
      <div class="text-xs font-medium text-gray-900 dark:text-gray-100 line-clamp-2">{{ title }}</div>
      <!-- The parent report, not the author: on this page the question is
           "which report is this dashboard part of", and the report page names
           the author anyway. -->
      <div class="mt-1 text-[10px] text-gray-500 dark:text-gray-400 truncate">{{ artifact.report_title || $t('dashboards.untitledReport') }}</div>
    </div>
  </NuxtLink>
</template>

<script setup lang="ts">
// One ARTIFACT, one card. RecentReportCard is the report-grained sibling used
// on the home page; this one exists because /dashboards lists artifacts, where
// a report holding a dashboard + a doc + a deck must produce three cards and
// three badges rather than one card wearing whichever badge sorted first.
const props = defineProps<{
  artifact: {
    id: string
    report_id: string
    report_title?: string | null
    title?: string | null
    mode: string
    thumbnail_url?: string | null
  }
}>()

const { t } = useI18n()
const config = useRuntimeConfig()
const imageError = ref(false)

const title = computed(() => props.artifact.title || t('dashboards.untitledArtifact'))

const thumbnailUrl = computed(() => {
  if (!props.artifact.thumbnail_url) return null
  return `${config.public.baseURL}${props.artifact.thumbnail_url}`
})

// Deep link: the report page opens on this artifact rather than on whichever
// one it would otherwise default to.
const link = computed(() => `/r/${props.artifact.report_id}?artifact=${props.artifact.id}`)

const icon = computed(() => {
  if (props.artifact.mode === 'slides') return 'heroicons:presentation-chart-bar'
  if (props.artifact.mode === 'doc') return 'heroicons:document-text'
  return 'heroicons:chart-bar-square'
})

const style = computed(() => {
  if (props.artifact.mode === 'slides') {
    return { bg: 'bg-purple-100 dark:bg-purple-900/40', text: 'text-purple-700 dark:text-purple-300', label: t('dashboards.badgeSlides'), cardBg: 'bg-purple-50 dark:bg-purple-950', iconColor: 'text-purple-300 dark:text-purple-800' }
  }
  if (props.artifact.mode === 'doc') {
    return { bg: 'bg-emerald-100 dark:bg-emerald-900/40', text: 'text-emerald-700 dark:text-emerald-300', label: t('dashboards.badgeDoc'), cardBg: 'bg-emerald-50 dark:bg-emerald-950', iconColor: 'text-emerald-300 dark:text-emerald-800' }
  }
  return { bg: 'bg-blue-100 dark:bg-blue-900/40', text: 'text-blue-700 dark:text-blue-300', label: t('dashboards.badgeDashboard'), cardBg: 'bg-blue-50 dark:bg-blue-950', iconColor: 'text-blue-300 dark:text-blue-800' }
})
</script>
