<template>
  <div class="flex h-screen items-center justify-center text-sm text-gray-500 dark:text-gray-400">
    {{ $t('auth.redirecting') }}
  </div>
</template>

<script setup lang="ts">
// Public self-signup is disabled. Accounts are created by an administrator
// from Settings → Members. Anyone landing here is sent to the sign-in page.
import { onMounted } from 'vue'
import { definePageMeta, navigateTo } from '#imports'

definePageMeta({
  layout: 'users'
})

onMounted(() => {
  navigateTo('/users/sign-in', { replace: true })
})
</script>
