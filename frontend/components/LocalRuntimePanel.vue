<!--
  Local Runtime lives in Account Settings (the profile modal), not in the
  organization Settings area: pairing a laptop is a PERSONAL action, while
  everything under Settings is administration. Extracted from the old
  pages/settings/local-runtime.vue so both entry points render one component.
-->
<template>
    <div class="mt-6">
        <h2 class="text-lg font-medium text-gray-900 dark:text-white">{{ $t('settings.localRuntime.title') }}
            <p class="text-sm text-gray-500 dark:text-gray-400 font-normal mb-8">
                {{ $t('settings.localRuntime.subtitle') }}
            </p>
        </h2>

        <div v-if="loading" class="py-4 text-sm text-gray-500">{{ $t('common.loading') }}</div>

        <div v-else class="space-y-6 md:w-2/3">
            <!-- Status card -->
            <div class="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                <div class="flex items-center gap-3">
                    <span :class="['inline-block w-2.5 h-2.5 rounded-full', status.paired && status.online ? 'bg-green-500' : status.paired ? 'bg-amber-500' : 'bg-gray-300']"></span>
                    <div class="flex-1">
                        <div class="text-sm font-medium text-gray-900 dark:text-white">
                            <template v-if="!status.paired">{{ $t('settings.localRuntime.notPaired') }}</template>
                            <template v-else-if="status.online">{{ status.name }} — {{ $t('settings.localRuntime.online') }}</template>
                            <template v-else>{{ status.name }} — {{ $t('settings.localRuntime.offline') }}</template>
                        </div>
                        <div v-if="status.paired" class="text-xs text-gray-500 mt-0.5">
                            {{ $t('settings.localRuntime.lastSeen') }}: {{ status.last_seen ? new Date(status.last_seen + 'Z').toLocaleString() : '—' }}
                            <span v-if="status.helper_version"> · v{{ status.helper_version }}</span>
                        </div>
                    </div>
                    <UButton v-if="status.paired" color="red" variant="soft" size="xs" @click="unpair">{{ $t('settings.localRuntime.unpair') }}</UButton>
                </div>
            </div>

            <!-- Run-local toggle -->
            <div v-if="status.paired" class="flex items-center justify-between border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                <div>
                    <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.localRuntime.runLocal') }}</div>
                    <div class="text-xs text-gray-500 mt-0.5">{{ $t('settings.localRuntime.runLocalHint') }}</div>
                </div>
                <UToggle :model-value="status.run_local_enabled" @update:model-value="toggleRunLocal" />
            </div>

            <!-- Download the helper (one card per OS) -->
            <div class="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3">
                <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.localRuntime.downloadTitle') }}</div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div v-for="os in osCards" :key="os.key"
                         class="border border-gray-200 dark:border-gray-700 rounded-lg p-4 flex flex-col gap-2">
                        <div class="flex items-center gap-2">
                            <UIcon :name="os.icon" class="w-4 h-4 text-gray-400" />
                            <span class="text-sm font-medium text-gray-900 dark:text-white">{{ os.name }}</span>
                        </div>
                        <p class="text-xs text-gray-500 flex-1">{{ os.hint }}</p>
                        <UButton v-if="os.available !== false" size="sm" icon="i-heroicons-arrow-down-tray"
                                 :to="os.url" external target="_blank" class="self-start">
                            {{ os.cta }}
                        </UButton>
                        <UButton v-else size="sm" color="gray" variant="soft" disabled class="self-start">
                            {{ $t('settings.localRuntime.comingSoon') }}
                        </UButton>
                    </div>
                </div>
                <p class="text-[11px] text-gray-400">{{ $t('settings.localRuntime.unsignedNote') }}</p>
            </div>

            <!-- Pairing: plug & play -->
            <div class="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-4">
                <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('settings.localRuntime.pairTitle') }}</div>
                <ol class="text-xs text-gray-500 space-y-1 list-decimal list-inside">
                    <li>{{ $t('settings.localRuntime.step1') }}</li>
                    <li>{{ $t('settings.localRuntime.step2') }}</li>
                    <li>{{ $t('settings.localRuntime.step3') }}</li>
                </ol>
                <div class="flex items-center gap-3 flex-wrap">
                    <UButton size="sm" variant="soft" :loading="minting" @click="startPair">{{ pairCode ? $t('settings.localRuntime.newCode') : $t('settings.localRuntime.generateCode') }}</UButton>
                </div>
                <div v-if="pairCode" class="flex items-center gap-3">
                    <span class="font-mono text-3xl tracking-[0.35em] font-semibold text-gray-900 dark:text-white">{{ pairCode }}</span>
                    <span class="text-xs text-gray-500">{{ $t('settings.localRuntime.codeExpires') }}</span>
                </div>
                <details class="text-xs text-gray-500">
                    <summary class="cursor-pointer select-none">{{ $t('settings.localRuntime.advanced') }}</summary>
                    <p class="mt-2">{{ $t('settings.localRuntime.pairHint') }}</p>
                    <pre class="mt-2 text-[11px] bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-md p-3 overflow-x-auto">python3 helper.py pair {{ pairCode || '&lt;code&gt;' }} --server {{ origin }}</pre>
                </details>
            </div>

            <p v-if="error" class="text-xs text-red-500">{{ error }}</p>
            <p class="text-[11px] text-gray-400">{{ $t('settings.localRuntime.privacy') }}</p>
        </div>
    </div>
</template>

<script setup lang="ts">

const { t } = useI18n()
const { localRuntimeOn } = useAppSettings()
const loading = ref(true)
const error = ref('')
const minting = ref(false)
const pairCode = ref('')
const status = ref<any>({ paired: false })
const origin = computed(() => (process.client ? window.location.origin : ''))
let timer: any = null

// Download availability. The helper zips are build artifacts staged into
// frontend/public/downloads (gitignored), so a build may ship without one.
// null = not checked yet (button stays enabled), false = missing.
const downloads = [
    { key: 'mac', url: '/downloads/CityAgentHelper-mac.zip', icon: 'i-heroicons-computer-desktop', nameKey: 'settings.localRuntime.osMac', hintKey: 'settings.localRuntime.macHint', ctaKey: 'settings.localRuntime.downloadMac' },
    { key: 'win', url: '/downloads/CityAgentHelper-win.zip', icon: 'i-heroicons-squares-2x2', nameKey: 'settings.localRuntime.osWin', hintKey: 'settings.localRuntime.winHint', ctaKey: 'settings.localRuntime.downloadWin' },
]
const available = ref<Record<string, boolean | null>>({ mac: null, win: null })
const osCards = computed(() => downloads.map(d => ({
    ...d,
    name: t(d.nameKey),
    hint: t(d.hintKey),
    cta: t(d.ctaKey),
    available: available.value[d.key],
})))

async function checkDownload(key: string, url: string) {
    // HEAD is not usable: the SPA catch-all is a GET-only route (405), and a
    // missing file falls back to index.html with 200 — so `ok` proves nothing.
    // A 1-byte ranged GET is cheap and the content type tells the two apart.
    const controller = new AbortController()
    try {
        const res = await fetch(url, {
            method: 'GET',
            headers: { Range: 'bytes=0-0' },
            cache: 'no-store',
            signal: controller.signal,
        })
        const type = (res.headers.get('content-type') || '').toLowerCase()
        available.value[key] = res.ok && !type.includes('text/html')
    } catch (e: any) {
        if (e?.name !== 'AbortError') available.value[key] = false
    } finally {
        controller.abort() // never pull the body — these files are ~80 MB
    }
}

async function refresh() {
    try {
        const { data, error: e } = await useMyFetch<any>('/api/local-runtime/status', { method: 'GET' } as any)
        if ((e as any)?.value) throw (e as any).value
        status.value = (data as any)?.value || { paired: false }
        error.value = ''
    } catch (e: any) {
        error.value = e?.data?.detail || e?.message || 'Failed to load status'
    }
    loading.value = false
}

async function startPair() {
    minting.value = true
    try {
        const { data, error: e } = await useMyFetch<any>('/api/local-runtime/pair/start', { method: 'POST' } as any)
        if ((e as any)?.value) throw (e as any).value
        pairCode.value = (data as any)?.value?.code || ''
    } catch (e: any) {
        error.value = e?.data?.detail || e?.message || 'Failed to create code'
    }
    minting.value = false
}

async function toggleRunLocal(v: boolean) {
    await useMyFetch('/api/local-runtime/toggle', { method: 'PUT', body: { enabled: v } } as any)
    await refresh()
}

async function unpair() {
    await useMyFetch('/api/local-runtime', { method: 'DELETE' } as any)
    pairCode.value = ''
    await refresh()
}

onMounted(async () => {
    if (!localRuntimeOn.value) { navigateTo('/settings/general'); return }
    await refresh()
    downloads.forEach(d => checkDownload(d.key, d.url))
    timer = setInterval(refresh, 5000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
