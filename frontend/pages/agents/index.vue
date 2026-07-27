<template>
    <div class="text-sm h-full">
        <div class="w-full h-full">
            <KnowledgeExplorer />
        </div>
    </div>
</template>

<script setup lang="ts">
import KnowledgeExplorer from '~/components/KnowledgeExplorer.vue'

definePageMeta({
    auth: true,
    layout: 'default'
})

// Handle the OAuth callback redirect (the backend sends the browser back to
// /agents?oauth=success|error after token exchange). Surface the result as a
// toast and strip the query params so a refresh doesn't re-fire the toast.
const { t } = useI18n()
onMounted(() => {
    const route = useRoute()
    if (route.query.oauth === 'success') {
        // powerbi_mt leaves a client-side breadcrumb before its OAuth redirect
        // so we can reassure that every tenant was scanned & merged. Tenant tags
        // aren't on the client without a new backend call, so we keep it to a
        // generic merge note. Every other connector shows the plain "Connected".
        let pbimt = false
        try {
            pbimt = sessionStorage.getItem('pbimt.oauthPending') === '1'
            if (pbimt) sessionStorage.removeItem('pbimt.oauthPending')
        } catch (e) { /* sessionStorage unavailable */ }
        useToast().add(pbimt
            ? { title: t('data.connectedSuccess'), description: 'Your Power BI tenants were scanned and their tables merged.', color: 'green', icon: 'i-heroicons-check-circle' }
            : { title: t('data.connectedSuccess'), color: 'green', icon: 'i-heroicons-check-circle' })
        navigateTo('/agents', { replace: true })
    } else if (route.query.oauth === 'error') {
        useToast().add({
            title: t('data.connectionFailed'),
            description: (route.query.message as string) || '',
            color: 'red',
            icon: 'i-heroicons-x-circle',
        })
        navigateTo('/agents', { replace: true })
    }
})
</script>
