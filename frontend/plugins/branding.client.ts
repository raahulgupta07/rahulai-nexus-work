// branding.client.ts — applies instance branding to everything that lives
// OUTSIDE the Vue tree: the browser tab title, the favicon, and the accent
// colour CSS variables.
//
// WHY `enforce: 'post'`
//   `plugins/settings.ts` is the one place that fetches the public
//   `GET /api/settings` feed, and it awaits that fetch before providing
//   `nuxtApp.$settings`. A 'post' plugin runs after every normal plugin has
//   resolved, so `$settings` is guaranteed to be populated here and we never add
//   a second network call on boot.
//
// WHY THE TITLE MUST BE SET HERE
//   `nuxt.config.ts` hardcodes `app.head.title` and
//   `app.head.titleTemplate: '%s · CityAgent Insights'`. Those are compiled into
//   the bundle at build time, so a database value can never reach them. They stay
//   exactly as they are and act as the pre-hydration fallback; this plugin
//   re-declares both at runtime via `useHead`, which takes priority over the
//   config-level head, so the tab reflects the configured name — including the
//   `%s · <name>` suffix on every routed page, not just the base title.

import { watchEffect } from 'vue'
// composables/ IS auto-imported (nuxt.config's `imports.dirs` only ADDS
// ee/composables; it does not narrow the defaults). Imported explicitly anyway:
// this plugin runs on every page load, and a resolution failure here would mean
// a wrong tab title on every install. Nuxt skips auto-import for names that are
// already explicitly imported, so there is no duplicate-declaration risk.
import { BRANDING_DEFAULTS, setBranding, useBranding } from '~/composables/useBranding'

/** Darken a #rrggbb by `amount` (0..1) — used for the accent hover shade. */
function darkenHex(hex: string, amount: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) =>
    Math.max(0, Math.min(255, Math.round(c * (1 - amount)))),
  )
  return `#${ch.map((c) => c.toString(16).padStart(2, '0')).join('')}`
}

/** "#2563eb" -> "37, 99, 235", so stylesheets can build rgba() tints. */
function hexToRgbChannels(hex: string): string | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`
}

/**
 * Point every existing `<link rel="icon">` at `href`, adding one if the document
 * has none. Only called when a favicon is actually configured — otherwise the
 * document's own tags are left untouched and the default `/favicon.ico` stands.
 */
function applyFavicon(href: string): void {
  const head = document.head
  if (!head) return
  const links = Array.from(
    head.querySelectorAll<HTMLLinkElement>('link[rel~="icon"], link[rel="shortcut icon"]'),
  )
  if (links.length === 0) {
    const link = document.createElement('link')
    link.rel = 'icon'
    link.setAttribute('data-brand-favicon', '')
    link.href = href
    head.appendChild(link)
    return
  }
  for (const link of links) {
    // Drop a stale type hint (e.g. image/x-icon) — the uploaded icon may be PNG.
    link.removeAttribute('type')
    link.href = href
  }
}

export default defineNuxtPlugin({
  name: 'branding',
  enforce: 'post',
  setup(nuxtApp) {
    // Reuse the boot fetch from plugins/settings.ts. The useAppSettings state is
    // read directly (not via the composable) so we never trigger its auto-fetch.
    const provided = (nuxtApp as any).$settings
    const cached = useState<any>('app-settings').value
    setBranding(provided?.branding ?? cached?.branding ?? null)

    const { productName, faviconUrl, accentColor } = useBranding()

    // Runtime title + titleTemplate. Getters keep both reactive, so a later
    // branding change (e.g. saved from the admin page) retitles the tab.
    useHead({
      title: () => productName.value,
      titleTemplate: (chunk?: string) =>
        chunk ? `${chunk} · ${productName.value}` : productName.value,
    })

    watchEffect(() => {
      const root = document.documentElement
      if (!root) return

      // Accent colour. See the report/comment in useBranding: the app expresses
      // its blue as Tailwind utility classes and literal hex, not as a variable,
      // so these custom properties are published for stylesheets that opt in
      // (today: pages/users/sign-in.vue). No global class refactor is attempted.
      const accent = accentColor.value
      root.style.setProperty('--brand-accent', accent)
      root.style.setProperty('--brand-accent-hover', darkenHex(accent, 0.12))
      const rgb = hexToRgbChannels(accent)
      if (rgb) root.style.setProperty('--brand-accent-rgb', rgb)

      // Favicon: only touch the document when one is actually configured.
      if (faviconUrl.value && faviconUrl.value !== BRANDING_DEFAULTS.faviconUrl) {
        applyFavicon(faviconUrl.value)
      }
    })
  },
})
