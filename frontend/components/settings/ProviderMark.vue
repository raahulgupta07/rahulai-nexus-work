<script setup lang="ts">
// Brand marks for identity providers, drawn inline.
//
// ★These are DRAWN APPROXIMATIONS, not official assets. Google, Microsoft,
// Keycloak, Okta and Auth0 each publish brand kits with usage rules (minimum
// size, clear space, permitted recolouring); replace these with the official
// artwork before any public release.
// ★Inline SVG, never a hosted image — the app serves a strict CSP, so an
// external <img> src is blocked and the row renders an empty chip.

const props = withDefaults(defineProps<{
  icon?: string
  label?: string
  size?: number
}>(), {
  icon: '',
  label: '',
  size: 24,
})

const KNOWN = ['google', 'entra', 'keycloak', 'oidc', 'ldap', 'okta', 'auth0', 'generic']

// Unknown or missing icon falls back to the letter chip the page rendered
// before this component existed, so a provider we have no mark for is
// unchanged rather than blank.
const hasMark = computed(() => KNOWN.includes(props.icon))

const letter = computed(() => (props.label || props.icon || '?').charAt(0).toUpperCase())

// The page's former `ssoChipClass` colours, moved here with it. Keeping them
// identical means a provider we have no artwork for renders exactly as it did
// before this component existed.
const letterClass = computed(() => {
  const byIcon: Record<string, string> = {
    google: 'bg-red-500',
    keycloak: 'bg-indigo-500',
    entra: 'bg-sky-600',
    oidc: 'bg-slate-500',
  }
  return byIcon[props.icon] || 'bg-slate-500'
})

// Tailwind cannot build a class from a runtime number, so the geometry is
// inline while the colours stay utility classes.
const box = computed(() => ({ width: `${props.size}px`, height: `${props.size}px` }))
const glyph = computed(() => Math.round(props.size * 0.66))
</script>

<template>
  <!-- Marked-up tile: full-colour glyph on a neutral surface that follows dark mode. -->
  <span
    v-if="hasMark"
    class="inline-flex items-center justify-center rounded flex-shrink-0 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700"
    :style="box"
  >
    <!-- Decorative: every row already shows the provider name as text. -->
    <svg v-if="icon === 'google'" :width="glyph" :height="glyph" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.8c-.5 2.7-2.1 5-4.4 6.6v5.5h7.1c4.2-3.8 6.6-9.5 6.6-16.1z" />
      <path fill="#34A853" d="M24 46c6 0 11-2 14.6-5.4l-7.1-5.5c-2 1.3-4.5 2.1-7.5 2.1-5.8 0-10.6-3.9-12.4-9.1H4.3v5.7C7.9 41 15.4 46 24 46z" />
      <path fill="#FBBC05" d="M11.6 28.1c-.5-1.3-.7-2.7-.7-4.1s.3-2.8.7-4.1v-5.7H4.3C2.8 17.2 2 20.5 2 24s.8 6.8 2.3 9.8l7.3-5.7z" />
      <path fill="#EA4335" d="M24 10.8c3.3 0 6.2 1.1 8.5 3.3l6.3-6.3C34.9 4.2 30 2 24 2 15.4 2 7.9 7 4.3 14.2l7.3 5.7c1.8-5.2 6.6-9.1 12.4-9.1z" />
    </svg>

    <svg v-else-if="icon === 'entra'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="1" y="1" width="10" height="10" fill="#F25022" />
      <rect x="13" y="1" width="10" height="10" fill="#7FBA00" />
      <rect x="1" y="13" width="10" height="10" fill="#00A4EF" />
      <rect x="13" y="13" width="10" height="10" fill="#FFB900" />
    </svg>

    <svg v-else-if="icon === 'keycloak'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4D4D4D" d="M12 1.5 3 5v7c0 5 3.8 9.4 9 10.5 5.2-1.1 9-5.5 9-10.5V5l-9-3.5z" />
      <path fill="#008AAA" d="M12 3.4 4.8 6.2V12c0 4 3 7.6 7.2 8.6V3.4z" />
      <circle cx="12" cy="10.3" r="2.2" fill="#fff" />
      <path fill="#fff" d="M11.1 11.6h1.8v5.1h-1.8z" />
    </svg>

    <svg v-else-if="icon === 'oidc'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <ellipse cx="10.5" cy="14.5" rx="8.5" ry="5" fill="none" stroke="#F78C40" stroke-width="1.8" />
      <path fill="#F78C40" d="M11.9 2.2 15.6 4l-3.7 1.8v14h-2.2V4.2l2.2-2z" />
      <path fill="#F78C40" d="M15.8 8.6 21.5 11l-5.7 2.4z" />
    </svg>

    <!-- Ours, so no trademark to respect: a directory tree, one root over two leaves. -->
    <svg v-else-if="icon === 'ldap'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path stroke="#4F46E5" stroke-width="1.6" stroke-linecap="round" fill="none" d="M12 7v4M6 18v-3h12v3M12 11v4" />
      <rect x="8.5" y="2" width="7" height="5" rx="1" fill="#4F46E5" />
      <rect x="2.5" y="17.5" width="7" height="4.5" rx="1" fill="#818CF8" />
      <rect x="14.5" y="17.5" width="7" height="4.5" rx="1" fill="#818CF8" />
    </svg>

    <svg v-else-if="icon === 'okta'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" fill="#007DC1" />
      <circle cx="12" cy="12" r="4" fill="#fff" />
    </svg>

    <svg v-else-if="icon === 'auth0'" :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#EB5424" d="M12 1.5 21 5v6.7c0 4.8-3.6 9.2-9 10.8-5.4-1.6-9-6-9-10.8V5l9-3.5z" />
      <path fill="#fff" d="m12 6.4 1.9 5.7h-3.8L12 6.4zM8.6 14.1h6.8l1.2 3.6L12 15l-5.6 2.7 1.2-3.6z" />
    </svg>

    <svg v-else :width="glyph" :height="glyph" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="#64748B" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  </span>

  <!-- Fallback: the original letter chip, unchanged. -->
  <span
    v-else
    class="inline-flex items-center justify-center rounded text-[10px] font-bold text-white flex-shrink-0"
    :class="letterClass"
    :style="box"
  >{{ letter }}</span>
</template>
