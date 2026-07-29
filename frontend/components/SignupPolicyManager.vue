<!--
  How people get in.

  This screen used to be "Domain-based signup" — a list of email domains an
  admin maintained by hand. That list only ever existed because nothing else
  could admit anybody: signing in proved who you were and never decided whether
  you got an account. Now three things can, and a hand-kept domain list is a
  second copy of what the identity provider already knows, in a place that can
  disagree with it.

  So the page states the three real ways in, points at where each is configured,
  and keeps the domain list under Advanced for installs that still lean on it.

  ★The single sign-on and directory switches are shown here and set on the
  Identity Providers page. Deliberately not editable in both places — that is
  exactly the two-places-to-disagree problem this page exists to remove.
-->
<template>
    <div class="mt-4 max-w-2xl">
        <h3 class="text-base font-medium text-gray-900 dark:text-white">{{ $t('waysIn.title') }}</h3>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ $t('waysIn.description') }}</p>

        <!-- The three doors -->
        <div class="mt-5 space-y-3">
            <!-- 1. An admin adds them -->
            <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-4 py-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('waysIn.adminTitle') }}</div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('waysIn.adminHint') }}</p>
                    </div>
                    <span class="shrink-0 text-[11px] rounded-full px-2 py-0.5 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300">
                        {{ $t('waysIn.badgeAlwaysOn') }}
                    </span>
                </div>
                <NuxtLink to="/settings/members" class="inline-block mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline">
                    {{ $t('waysIn.adminLink') }}
                </NuxtLink>
            </div>

            <!-- 2. Single sign-on -->
            <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-4 py-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('waysIn.ssoTitle') }}</div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('waysIn.ssoHint') }}</p>
                    </div>
                    <span class="shrink-0 text-[11px] rounded-full px-2 py-0.5" :class="badgeClass(ssoDoor)">
                        {{ badgeLabel(ssoDoor) }}
                    </span>
                </div>

                <ul v-if="ssoProviders.length" class="mt-2.5 space-y-1">
                    <li v-for="p in ssoProviders" :key="p.name" class="flex items-center justify-between text-xs">
                        <span class="text-gray-700 dark:text-gray-300">{{ p.label }}</span>
                        <span :class="p.creates
                            ? 'text-green-700 dark:text-green-400'
                            : 'text-gray-500 dark:text-gray-400'">
                            {{ p.creates ? $t('waysIn.providerCreates') : $t('waysIn.providerInviteOnly') }}
                        </span>
                    </li>
                </ul>

                <NuxtLink to="/settings/identity-provider" class="inline-block mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline">
                    {{ ssoProviders.length ? $t('waysIn.ssoLink') : $t('waysIn.ssoLinkSetup') }}
                </NuxtLink>
            </div>

            <!-- 3. The directory -->
            <div class="rounded-lg border border-gray-200 dark:border-gray-800 px-4 py-3">
                <div class="flex items-start justify-between gap-3">
                    <div>
                        <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('waysIn.ldapTitle') }}</div>
                        <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('waysIn.ldapHint') }}</p>
                    </div>
                    <span class="shrink-0 text-[11px] rounded-full px-2 py-0.5" :class="badgeClass(ldapDoor)">
                        {{ badgeLabel(ldapDoor) }}
                    </span>
                </div>
                <NuxtLink to="/settings/identity-provider" class="inline-block mt-2 text-xs text-blue-600 dark:text-blue-400 hover:underline">
                    {{ ldapDoor === 'off' ? $t('waysIn.ldapLinkSetup') : $t('waysIn.ldapLink') }}
                </NuxtLink>
            </div>
        </div>

        <!-- The role they arrive with -->
        <div class="mt-6 pt-5 border-t border-gray-100 dark:border-gray-800">
            <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('waysIn.roleTitle') }}</div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{{ $t('waysIn.roleHint') }}</p>

            <div
                v-if="!anyDoorCreates"
                class="mt-3 rounded-md border border-gray-200 dark:border-gray-800 px-3 py-2 text-xs text-gray-500 dark:text-gray-400"
            >
                {{ $t('waysIn.roleUnusedNote') }}
            </div>

            <div class="mt-3 flex items-center gap-2">
                <USelectMenu
                    v-if="roles.length"
                    v-model="autoRole"
                    :options="roles.map((r) => r.name)"
                    size="sm"
                    class="w-60"
                />
                <input
                    v-else
                    v-model="autoRole"
                    type="text"
                    class="w-60 text-sm border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
                <UButton
                    color="blue"
                    size="sm"
                    :loading="savingRole"
                    :disabled="autoRole === originalAutoRole"
                    @click="saveRole"
                >
                    {{ $t('waysIn.save') }}
                </UButton>
            </div>
        </div>

        <!-- Advanced: the old domain list -->
        <div v-if="domainSignupAvailable" class="mt-6 pt-5 border-t border-gray-100 dark:border-gray-800">
            <button
                class="flex items-center gap-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white"
                @click="showAdvanced = !showAdvanced"
            >
                <Icon :name="showAdvanced ? 'heroicons:chevron-down' : 'heroicons:chevron-right'" class="h-3.5 w-3.5" />
                {{ $t('waysIn.advancedTitle') }}
            </button>

            <div v-if="showAdvanced" class="mt-3">
                <p class="text-xs text-gray-500 dark:text-gray-400">{{ $t('waysIn.advancedHint') }}</p>

                <div
                    v-if="globalUninvitedDisabled && form.enabled"
                    class="mt-3 rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950 px-3 py-2 text-xs text-amber-800"
                >
                    <b>{{ $t('signupPolicy.headsUpPrefix') }}</b> {{ $t('signupPolicy.headsUpMiddle') }}
                    <code class="font-mono">allow_uninvited_signups</code> {{ $t('signupPolicy.headsUpFlagOff') }} <b>{{ $t('signupPolicy.headsUpOff') }}</b>{{ $t('signupPolicy.headsUpSuffix') }}
                </div>

                <div class="mt-4 flex items-center justify-between">
                    <div>
                        <div class="text-sm font-medium text-gray-900 dark:text-white">{{ $t('signupPolicy.enable') }}</div>
                        <div class="text-xs text-gray-500 dark:text-gray-400">{{ $t('signupPolicy.enableHint') }}</div>
                    </div>
                    <UToggle v-model="form.enabled" />
                </div>

                <div class="mt-4">
                    <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">{{ $t('signupPolicy.allowedDomains') }}</label>
                    <div class="flex items-center gap-2">
                        <input
                            v-model="domainInput"
                            type="text"
                            :placeholder="$t('signupPolicy.domainPlaceholder')"
                            class="flex-1 text-sm border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                            @keydown.enter.prevent="addDomain"
                            @keydown.,.prevent="addDomain"
                        />
                        <UButton size="xs" variant="solid" color="blue" @click="addDomain">{{ $t('signupPolicy.add') }}</UButton>
                    </div>
                    <div v-if="form.allowed_domains.length" class="flex flex-wrap gap-2 mt-3">
                        <span
                            v-for="d in form.allowed_domains"
                            :key="d"
                            class="inline-flex items-center gap-1.5 text-xs bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-full px-2.5 py-1"
                        >
                            {{ d }}
                            <button
                                class="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
                                @click="removeDomain(d)"
                                :aria-label="$t('signupPolicy.removeDomainAria')"
                            >
                                <Icon name="heroicons:x-mark" class="h-3.5 w-3.5" />
                            </button>
                        </span>
                    </div>
                    <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">{{ $t('signupPolicy.domainsHint') }}</p>
                </div>

                <div class="mt-4">
                    <label class="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">{{ $t('signupPolicy.autoInviteRole') }}</label>
                    <USelectMenu
                        v-if="roles.length"
                        v-model="form.auto_invite_role"
                        :options="roles.map((r) => r.name)"
                        size="sm"
                        class="w-60"
                    />
                    <input
                        v-else
                        v-model="form.auto_invite_role"
                        type="text"
                        class="w-60 text-sm border border-gray-300 dark:border-gray-600 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    />
                    <p class="mt-2 text-xs text-gray-500 dark:text-gray-400">{{ $t('signupPolicy.roleHint') }}</p>
                </div>

                <div class="mt-5 flex items-center justify-between pt-4 border-t border-gray-100 dark:border-gray-800">
                    <p class="text-xs text-gray-500 dark:text-gray-400">{{ $t('signupPolicy.removeHint') }}</p>
                    <UButton color="blue" size="sm" :loading="saving" :disabled="!isDirty" @click="save">
                        {{ $t('signupPolicy.save') }}
                    </UButton>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { useAppSettings } from '~/composables/useAppSettings'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps<{
    organization: { id: string; name: string }
}>()

const toast = useToast()

type Policy = { enabled: boolean; allowed_domains: string[]; auto_invite_role: string }
const form = reactive<Policy>({ enabled: false, allowed_domains: [], auto_invite_role: 'member' })
const original = ref<Policy>({ enabled: false, allowed_domains: [], auto_invite_role: 'member' })

const domainInput = ref('')
const saving = ref(false)
const savingRole = ref(false)
const showAdvanced = ref(false)
const roles = ref<{ id: string; name: string }[]>([])

const autoRole = ref('member')
const originalAutoRole = ref('member')

// 'creates' — this door makes accounts on its own.
// 'invite'  — it authenticates people who were already invited.
// 'off'     — not set up.
type DoorState = 'creates' | 'invite' | 'off'
const ssoDoor = ref<DoorState>('off')
const ldapDoor = ref<DoorState>('off')
const ssoProviders = ref<{ name: string; label: string; creates: boolean }[]>([])

const { settings: appSettings, fetchSettings } = useAppSettings()
const globalUninvitedDisabled = computed(
    () => appSettings.value?.features?.allow_uninvited_signups === false,
)

// ★The tab itself is no longer gated on this feature — the role setting and the
// three doors must be reachable without it. Only the domain list is.
const domainSignupAvailable = ref(true)

const anyDoorCreates = computed(() => ssoDoor.value === 'creates' || ldapDoor.value === 'creates')

const isDirty = computed(() => JSON.stringify(form) !== JSON.stringify(original.value))

function badgeClass(state: DoorState) {
    if (state === 'creates') return 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300'
    if (state === 'invite') return 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
    return 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'
}

function badgeLabel(state: DoorState) {
    if (state === 'creates') return t('waysIn.badgeCreates')
    if (state === 'invite') return t('waysIn.badgeInviteOnly')
    return t('waysIn.badgeOff')
}

async function loadPolicy() {
    try {
        const { data, error } = await useMyFetch('/organization/signup-policy')
        if (error.value) {
            // 402 = no enterprise license for domain signup. Hide the section
            // rather than showing a control that cannot be saved.
            if ((error.value as any)?.statusCode === 402) domainSignupAvailable.value = false
            return
        }
        if (data.value) {
            const p = data.value as Policy
            Object.assign(form, p)
            original.value = JSON.parse(JSON.stringify(p))
            // An install already leaning on the domain list should not have to
            // hunt for it — open Advanced when it is actually in use.
            if (p.enabled) showAdvanced.value = true
        }
    } catch {
        domainSignupAvailable.value = false
    }
}

async function loadAutoRole() {
    try {
        const { data } = await useMyFetch('/organization/auto-provision')
        const r = (data.value as any)?.role
        if (r) {
            autoRole.value = r
            originalAutoRole.value = r
        }
    } catch {
        // leave the default; the resolver falls back to member too
    }
}

async function saveRole() {
    savingRole.value = true
    try {
        const { data, error } = await useMyFetch('/organization/auto-provision', {
            method: 'PUT',
            body: { role: autoRole.value },
        })
        if (error.value) throw error.value
        const r = (data.value as any)?.role
        if (r) {
            autoRole.value = r
            originalAutoRole.value = r
        }
        toast.add({ title: t('waysIn.toastRoleSaved'), color: 'green' })
    } catch (e: any) {
        const msg = e?.data?.detail || e?.message || t('waysIn.toastRoleFailed')
        toast.add({ title: msg, color: 'red' })
        autoRole.value = originalAutoRole.value
    } finally {
        savingRole.value = false
    }
}

async function loadDoors() {
    try {
        const { data } = await useMyFetch('/enterprise/sso/config')
        const cfg = data.value as any
        const list: { name: string; label: string; creates: boolean }[] = []
        if (cfg?.google?.enabled) {
            list.push({ name: 'google', label: 'Google', creates: !!cfg.google.auto_provision })
        }
        for (const p of cfg?.providers || []) {
            if (!p?.enabled) continue
            list.push({ name: p.name, label: p.label || p.name, creates: !!p.auto_provision })
        }
        ssoProviders.value = list
        ssoDoor.value = list.length === 0 ? 'off' : (list.some((p) => p.creates) ? 'creates' : 'invite')
    } catch {
        ssoProviders.value = []
        ssoDoor.value = 'off'
    }

    try {
        const { data } = await useMyFetch('/enterprise/ldap/config')
        const cfg = data.value as any
        if (!cfg?.enabled) ldapDoor.value = 'off'
        else ldapDoor.value = cfg.auto_provision_users ? 'creates' : 'invite'
    } catch {
        ldapDoor.value = 'off'
    }
}

function normalizeDomain(raw: string): string | null {
    const d = (raw || '').trim().toLowerCase()
    if (!d) return null
    if (d.includes('@') || d.includes('*') || /\s/.test(d)) return null
    if (!d.includes('.') || d.length > 253) return null
    return d
}

function addDomain() {
    const parts = domainInput.value.split(',')
    for (const p of parts) {
        const d = normalizeDomain(p)
        if (!d) continue
        if (!form.allowed_domains.includes(d)) form.allowed_domains.push(d)
    }
    domainInput.value = ''
}

function removeDomain(d: string) {
    form.allowed_domains = form.allowed_domains.filter((x) => x !== d)
}

async function loadRoles() {
    try {
        const { data } = await useMyFetch(`/organizations/${props.organization.id}/roles`)
        if (data.value) roles.value = data.value as { id: string; name: string }[]
    } catch {
        roles.value = []
    }
}

async function save() {
    if (form.enabled && form.allowed_domains.length === 0) {
        toast.add({ title: t('signupPolicy.toastAddDomain'), color: 'amber' })
        return
    }
    saving.value = true
    try {
        const { data, error } = await useMyFetch('/organization/signup-policy', {
            method: 'PUT',
            body: { ...form },
        })
        if (error.value) throw error.value
        if (data.value) {
            const p = data.value as Policy
            Object.assign(form, p)
            original.value = JSON.parse(JSON.stringify(p))
            toast.add({ title: t('signupPolicy.toastSaved'), color: 'green' })
        }
    } catch (e: any) {
        const msg = e?.data?.detail || e?.message || t('signupPolicy.toastFailed')
        toast.add({ title: msg, color: 'red' })
    } finally {
        saving.value = false
    }
}

onMounted(() => {
    fetchSettings()
    loadPolicy()
    loadRoles()
    loadAutoRole()
    loadDoors()
})
</script>
