<template>
    <div class="mt-6">
        <h2 class="text-lg font-medium text-gray-900 dark:text-white">{{ $t('settings.accessPage.title') }}
            <p class="text-sm text-gray-500 dark:text-gray-400 font-normal mb-8">
                {{ $t('settings.accessPage.subtitle') }}
            </p>
        </h2>

        <div v-if="loading" class="py-4">
            <ULoader />
        </div>

        <UAlert v-if="error" class="mt-4" type="danger">
            {{ error }}
        </UAlert>

        <div v-if="!loading && !error" class="space-y-6 md:w-2/3">
            <div
                v-for="item in items"
                :key="item.key"
                class="border border-gray-200 dark:border-gray-800 rounded-lg p-4"
            >
                <div class="flex items-start justify-between gap-4">
                    <div class="min-w-0">
                        <div class="font-medium text-gray-900 dark:text-gray-100 flex items-center gap-2">
                            {{ item.feature.name }}
                            <span
                                :class="badgeClass(stateOf(item.key))"
                                class="inline-flex items-center px-1.5 h-4 rounded text-[10px] font-semibold"
                            >{{ badgeLabel(stateOf(item.key)) }}</span>
                        </div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1 max-w-prose">
                            {{ item.blurb }}
                        </p>
                    </div>
                </div>

                <!-- Three-state selector. A plain toggle cannot express
                     "built, not released yet", which is the whole point. -->
                <div class="mt-3 inline-flex rounded-md bg-gray-100 dark:bg-gray-800 p-0.5 gap-0.5">
                    <button
                        v-for="opt in STATES"
                        :key="opt"
                        type="button"
                        :disabled="saving === item.key"
                        :aria-pressed="stateOf(item.key) === opt"
                        class="px-3 py-1 text-xs font-medium rounded cursor-pointer transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        :class="stateOf(item.key) === opt
                            ? 'bg-white dark:bg-gray-900 shadow-sm ' + activeTextClass(opt)
                            : 'text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'"
                        @click="setState(item.key, opt)"
                    >{{ $t('settings.accessPage.state.' + opt) }}</button>
                </div>

                <p class="text-[11px] text-gray-500 dark:text-gray-400 mt-2">
                    {{ $t('settings.accessPage.effect.' + stateOf(item.key)) }}
                </p>
            </div>

            <p class="text-[11px] text-gray-400 dark:text-gray-500">
                {{ $t('settings.accessPage.footnote') }}
            </p>
        </div>
    </div>
</template>

<script setup lang="ts">
definePageMeta({ layout: 'settings' })

const { t } = useI18n()
const toast = useToast()

// Keys are the org-settings field names; order is the order shown.
const KEYS = ['local_folders', 'api_keys', 'mcp_enabled'] as const
const STATES = ['on', 'coming_soon', 'off'] as const
type AccessState = typeof STATES[number]

const loading = ref(true)
const error = ref('')
const saving = ref<string | null>(null)
const features = ref<Record<string, any>>({})

// Mirrors the backend's access_state(): the stored value may be a plain
// boolean on an organisation that predates the three-state switch (mcp_enabled
// shipped as a bool), and anything unreadable must read as "off" rather than
// silently granting access.
function normalise(value: any): AccessState {
    if (typeof value === 'boolean') return value ? 'on' : 'off'
    if (typeof value === 'string') {
        const v = value.trim().toLowerCase().replace(/[-\s]/g, '_')
        if ((STATES as readonly string[]).includes(v)) return v as AccessState
        if (['true', 'enabled', 'yes'].includes(v)) return 'on'
        if (['soon', 'comingsoon'].includes(v)) return 'coming_soon'
    }
    return 'off'
}

const stateOf = (key: string): AccessState => normalise(features.value[key]?.value)

const items = computed(() =>
    KEYS.filter(k => features.value[k]).map(k => ({
        key: k,
        feature: features.value[k],
        blurb: t('settings.accessPage.blurb.' + k),
    }))
)

function badgeLabel(s: AccessState) { return t('settings.accessPage.state.' + s) }
function badgeClass(s: AccessState) {
    if (s === 'on') return 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-400'
    if (s === 'coming_soon') return 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-400'
    return 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
}
function activeTextClass(s: AccessState) {
    if (s === 'on') return 'text-green-700 dark:text-green-400'
    if (s === 'coming_soon') return 'text-amber-700 dark:text-amber-400'
    return 'text-gray-900 dark:text-gray-100'
}

async function fetchSettings() {
    loading.value = true
    error.value = ''
    try {
        const response = await useMyFetch('/api/organization/settings')
        if (response.status.value !== 'success') throw new Error(t('settings.accessPage.fetchError'))
        const config = (response.data?.value as any)?.config || {}
        const next: Record<string, any> = {}
        for (const k of KEYS) if (config[k]) next[k] = config[k]
        features.value = next
    } catch (e: any) {
        error.value = e?.message || t('settings.accessPage.fetchError')
    } finally {
        loading.value = false
    }
}

async function setState(key: string, next: AccessState) {
    const previous = stateOf(key)
    if (previous === next) return
    saving.value = key
    // Optimistic, so the selector responds immediately; rolled back on failure.
    features.value[key] = { ...features.value[key], value: next }
    try {
        // Always through the validating settings API. Writing this blob with
        // raw SQL drops FeatureConfig's required `description` and every later
        // settings read 500s — the whole settings surface, not just this key.
        const response = await useMyFetch('/api/organization/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config: { [key]: { value: next } } }),
        })
        if (response.status.value !== 'success') {
            const data = response.error?.value?.data
            throw new Error(data?.message || data?.detail || t('settings.accessPage.updateError'))
        }
        const updated = (response.data?.value as any)?.config?.[key]
        if (updated) features.value[key] = updated

        toast.add({
            title: t('settings.accessPage.toastSaved'),
            description: t('settings.accessPage.toastSavedBody', {
                name: features.value[key]?.name || key,
                state: t('settings.accessPage.state.' + next),
            }),
            color: 'green',
            timeout: 3000,
        })
    } catch (e: any) {
        features.value[key] = { ...features.value[key], value: previous }
        toast.add({
            title: t('settings.accessPage.toastFailed'),
            description: e?.message || t('settings.accessPage.updateError'),
            color: 'red',
            timeout: 5000,
        })
    } finally {
        saving.value = null
    }
}

onMounted(fetchSettings)
</script>
