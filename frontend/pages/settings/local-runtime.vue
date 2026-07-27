<template>
    <LocalRuntimePanel />
</template>

<script setup lang="ts">
// The Local Runtime UI now lives in Account Settings (the profile modal) — see
// components/LocalRuntimePanel.vue. This route is kept as a thin wrapper so any
// existing link or bookmark still resolves; it is no longer listed as a tab,
// because pairing your own computer is personal, not administration.
definePageMeta({ auth: true, layout: 'settings' })
</script>
