<template>
  <div class="flex h-screen justify-center py-20 px-5 sm:px-0">
    <div class="w-full sm:w-1/3 max-w-md">
      <h1 class="font-bold text-lg">{{ $t('auth.chooseNewPasswordTitle') }}</h1>

      <div class="mt-3 rounded-md border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/40 px-3 py-2.5 text-sm text-amber-700 dark:text-amber-300">
        {{ $t('auth.chooseNewPasswordNotice') }}
      </div>

      <form class="mt-5 space-y-4" @submit.prevent="submit">
        <div>
          <label class="block text-sm font-medium mb-1.5">{{ $t('profile.password.currentLabel') }}</label>
          <div class="relative">
            <input
              v-model="form.current_password"
              :type="show ? 'text' : 'password'"
              autocomplete="current-password"
              required
              class="border border-gray-300 dark:border-gray-600 rounded-lg px-4 py-2 w-full h-9 text-sm focus:outline-none focus:border-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
            />
          </div>
          <p class="mt-1 text-xs text-gray-500 dark:text-gray-400">{{ $t('auth.currentPasswordHint') }}</p>
        </div>

        <div>
          <label class="block text-sm font-medium mb-1.5">{{ $t('profile.password.newLabel') }}</label>
          <input
            v-model="form.new_password"
            :type="show ? 'text' : 'password'"
            autocomplete="new-password"
            required
            minlength="8"
            class="border border-gray-300 dark:border-gray-600 rounded-lg px-4 py-2 w-full h-9 text-sm focus:outline-none focus:border-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
          />
          <div class="flex gap-1 mt-1.5">
            <span
              v-for="i in 4"
              :key="i"
              class="h-1 flex-1 rounded-full transition-colors"
              :class="i <= strength.score ? strength.barClass : 'bg-gray-200 dark:bg-gray-700'"
            />
          </div>
          <span class="mt-1 block text-xs" :class="strength.textClass">{{ strength.label }}</span>
        </div>

        <div>
          <label class="block text-sm font-medium mb-1.5">{{ $t('profile.password.confirmLabel') }}</label>
          <input
            v-model="form.confirm"
            :type="show ? 'text' : 'password'"
            autocomplete="new-password"
            required
            class="border border-gray-300 dark:border-gray-600 rounded-lg px-4 py-2 w-full h-9 text-sm focus:outline-none focus:border-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
          />
          <p v-if="mismatch" class="mt-1 text-xs text-red-600">{{ $t('profile.password.doNotMatch') }}</p>
        </div>

        <label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400 cursor-pointer">
          <input type="checkbox" v-model="show" />
          {{ $t('auth.showPasswords') }}
        </label>

        <p v-if="error_message" class="text-red-500 text-sm">{{ error_message }}</p>

        <button
          type="submit"
          :disabled="isLoading || !valid"
          class="px-3 py-2 text-sm font-medium text-white bg-blue-700 hover:bg-blue-800 focus:ring-4 focus:outline-none focus:ring-blue-300 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ isLoading ? $t('auth.saving') : $t('auth.setPasswordAndContinue') }}
        </button>
      </form>

      <div class="mt-6 text-sm">
        <button class="text-blue-400 hover:text-blue-600" @click="signOutNow">
          {{ $t('auth.signOut') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Shown when a super admin set this account's password with "require a change
 * at next sign-in". The backend refuses every other path until it clears, so
 * this is the only screen the person can reach — see `_enforce_password_change`
 * in app/core/auth.py.
 *
 * It asks for the CURRENT password (the temporary one they were handed) as well
 * as the new one, because the change route is the same verified endpoint used
 * from the profile modal. One code path, one rule.
 */
import { definePageMeta } from '#imports'

definePageMeta({ layout: 'users' })

const { t } = useI18n()
const { getSession, signOut } = useAuth()

const form = ref({ current_password: '', new_password: '', confirm: '' })
const show = ref(false)
const isLoading = ref(false)
const error_message = ref('')

const mismatch = computed(() => form.value.confirm.length > 0 && form.value.new_password !== form.value.confirm)
const valid = computed(() =>
  form.value.current_password.length > 0 &&
  form.value.new_password.length >= 8 &&
  form.value.new_password === form.value.confirm
)
const strength = computed(() => passwordStrength(form.value.new_password, t))

async function submit() {
  if (!valid.value) return
  isLoading.value = true
  error_message.value = ''
  try {
    const resp = await useMyFetch('/users/me/change-password', {
      method: 'POST',
      body: {
        current_password: form.value.current_password,
        new_password: form.value.new_password,
      },
    })
    if (resp.error.value) {
      error_message.value = resp.error.value.data?.detail || t('profile.password.failed')
      return
    }
    // The flag is on the session; without a forced refresh the middleware would
    // read the stale copy and bounce straight back to this page.
    await getSession({ force: true })
    await navigateTo('/')
  } catch (e: any) {
    error_message.value = e?.message || t('profile.password.failed')
  } finally {
    isLoading.value = false
  }
}

async function signOutNow() {
  await signOut({ callbackUrl: '/users/sign-in' })
}
</script>
