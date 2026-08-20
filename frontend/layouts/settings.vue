<template>
    <NuxtLayout name="default">
        <div class="flex justify-center ps-2 md:ps-4 text-sm">
            <div class="w-full max-w-7xl px-4 ps-0 py-4">
                <div>
                    <h1 class="text-lg font-semibold">
                        {{ $t('settings.title') }}
                    </h1>
                    
                    <!-- Tabs navigation -->
                    <div class="border-b border-gray-200 dark:border-gray-700 mt-6">
                        <!-- One row, always. Twelve tabs needed 1251px in a 1248px container, so the
                             last one — the ACTIVE one — was clipped mid-word. gap-x-6 plus four
                             shortened labels brings it to ~927px. Do not switch this to wrap or
                             overflow-x-auto: a settings tab the user cannot see is a destination
                             they will not find. -->
                        <nav class="-mb-px flex gap-x-6">
                            <NuxtLink
                                v-for="tab in visibleTabs"
                                :key="tab.name"
                                :to="`/settings/${tab.name}`"
                                :class="[
                                    route.path === `/settings/${tab.name}`
                                        ? 'border-blue-500 text-blue-600'
                                        : 'border-transparent text-gray-500 dark:text-gray-400 hover:border-gray-300 hover:text-gray-700',
                                    'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium'
                                ]"
                            >
                                {{ $t(tab.label) }}
                            </NuxtLink>
                        </nav>
                    </div>

                    <!-- Page content -->
                    <slot />
                </div>
            </div>
        </div>
    </NuxtLayout>
</template>

<script setup lang="ts">
const route = useRoute()
const { hasFeature } = useEnterprise()
const { localRuntimeOn } = useAppSettings()

// All available tabs with their required permissions. `requiredFeature` (when
// set) additionally gates the tab on an enterprise license feature.
// `requiredFlag` gates on an app feature flag; tabs without requiredPermission
// are visible to any signed-in member (e.g. pairing their own laptop).
const allTabs = [
    // Members and People are ADMINISTRATION screens: they list every person in
    // the organization with their email, role, linked identity providers and
    // join date. `view_members` is a baseline permission every member holds (it
    // backs sharing pickers), so gating these on it handed the whole staff
    // directory — and a "Admin" sidebar entry leading to it — to ordinary
    // members. Administration screens gate on `manage_settings`.
    { name: 'members', label: 'settings.membersTab', requiredPermission: "manage_settings" },
    { name: 'people', label: 'settings.peopleTab', requiredPermission: "manage_settings" },
    { name: 'models', label: 'settings.llm', requiredPermission: "manage_llm" },
    { name: 'ai_settings', label: 'settings.aiSettings', requiredPermission: "manage_settings" },
    // Which features members can reach at all. Sits next to AI Settings because
    // both are org-wide switches, but kept separate: these decide who gets a
    // feature, not how the agent behaves.
    { name: 'access', label: 'settings.accessTab', requiredPermission: "manage_settings" },
    { name: 'pii', label: 'settings.piiTab', requiredPermission: "manage_settings", requiredFeature: "pii_protection" },
    { name: 'general', label: 'settings.general', requiredPermission: "manage_settings" },
    { name: "integrations", label: "settings.integrations.title", requiredPermission: "manage_settings" },
    { name: 'audit', label: 'settings.auditLogs', requiredPermission: "view_audit_logs" },
    { name: 'identity-provider', label: 'settings.identityProviderTab', requiredPermission: "manage_identity_providers" },
    { name: 'smtp', label: 'settings.smtpTab', requiredPermission: "manage_settings" },
    // Local Runtime moved to Account Settings (the profile modal): pairing your
    // own computer is personal, and this Settings area is administration only.
]

// Filter tabs based on user permissions + enterprise feature availability
const visibleTabs = computed(() => {
    return allTabs.filter(tab => {
        if (tab.requiredPermission && !useCan(tab.requiredPermission)) return false
        if (tab.requiredFeature && !hasFeature(tab.requiredFeature)) return false
        if (tab.requiredFlag === 'local_runtime' && !localRuntimeOn.value) return false
        return true
    })
})
</script> 