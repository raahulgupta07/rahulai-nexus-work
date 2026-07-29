// useBranding — the single source for instance branding (name, tagline, logo,
// favicon, accent colour).
//
// WHERE THE VALUES COME FROM
//   The public, unauthenticated feed `GET /api/settings` carries a top-level
//   `branding` object. `plugins/settings.ts` already fetches that feed once at
//   boot and provides it as `nuxtApp.$settings`; `plugins/branding.client.ts`
//   (enforce: 'post', so it runs after that fetch has resolved) hands the object
//   to `setBranding()`. No component ever fetches anything itself.
//
// PUBLIC API
//   const { productName, tagline, footerText, accentColor, logoUrl, faviconUrl,
//           branding } = useBranding()
//     -> every field is a `ComputedRef<string>` except `branding` (the raw
//        reactive object). Use them directly in templates:
//          <img :src="logoUrl" :alt="productName" />
//          <h1>sign in to {{ productName }}</h1>
//
//   getBranding()  -> the plain reactive object, for module-scope / non-setup
//                     code (utils, event handlers) that cannot call composables.
//   setBranding(raw) -> merge a `branding` payload in (the plugin calls this).
//   BRANDING_DEFAULTS -> today's literal values.
//
// FALLBACKS
//   Every field defaults to the exact string the app shipped with, so an
//   installation that configures nothing renders identically to before and
//   nothing ever flashes a placeholder: the defaults are in place from the very
//   first render, before /api/settings has answered.

import { computed, reactive } from 'vue'

/** Shape of the `branding` object on the public `/api/settings` feed. */
export interface BrandingPayload {
  product_name?: string | null
  tagline?: string | null
  footer_text?: string | null
  accent_color?: string | null
  logo_key?: string | null
  favicon_key?: string | null
}

/** Today's literal values — an unconfigured instance must look byte-identical. */
export const BRANDING_DEFAULTS = {
  productName: 'CityAgent Insights',
  tagline: 'Your AI analyst for data',
  footerText: '',
  accentColor: '#2563eb',
  /** Static asset used when no `logo_key` is configured. */
  logoUrl: '/assets/logo-128.png',
  /** Static asset used when no `favicon_key` is configured. */
  faviconUrl: '/favicon.ico',
} as const

/**
 * Uploaded branding icons are served by `backend/app/routes/branding.py`, which
 * is mounted at `prefix="/api"` in main.py — so the route is
 * `/api/general/icon/{key}` (NOT `/api/branding/...`).
 */
export function brandingIconUrl(key: string | null | undefined): string | null {
  if (!key) return null
  return `/api/general/icon/${encodeURIComponent(key)}`
}

/**
 * Resolved branding. Declared explicitly rather than inferred from
 * BRANDING_DEFAULTS: that object is `as const`, so inference would give each
 * field a LITERAL type (`productName: "CityAgent Insights"`) and every
 * assignment in setBranding() would be a type error. The `as const` is still
 * wanted on the defaults themselves — it just must not be the state's type.
 */
export interface BrandingState {
  productName: string
  tagline: string
  footerText: string
  accentColor: string
  logoUrl: string
  faviconUrl: string
}

// Module-scope reactive state. The app is a static SPA (`ssr: false`), so there
// is exactly one client per module instance — no cross-request leakage risk, and
// utils outside a Nuxt setup context can read it safely.
const state: BrandingState = reactive<BrandingState>({
  productName: BRANDING_DEFAULTS.productName,
  tagline: BRANDING_DEFAULTS.tagline,
  footerText: BRANDING_DEFAULTS.footerText,
  accentColor: BRANDING_DEFAULTS.accentColor,
  logoUrl: BRANDING_DEFAULTS.logoUrl,
  faviconUrl: BRANDING_DEFAULTS.faviconUrl,
})

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/**
 * Merge a `branding` payload into the shared state. Missing / null / blank
 * fields keep their default, so a partial payload can never blank the UI.
 * `footer_text` is deliberately allowed to be empty — that IS its default.
 */
export function setBranding(raw: BrandingPayload | null | undefined): void {
  if (!raw || typeof raw !== 'object') return
  state.productName = str(raw.product_name) ?? BRANDING_DEFAULTS.productName
  state.tagline = str(raw.tagline) ?? BRANDING_DEFAULTS.tagline
  state.footerText =
    typeof raw.footer_text === 'string' ? raw.footer_text : BRANDING_DEFAULTS.footerText
  state.accentColor = str(raw.accent_color) ?? BRANDING_DEFAULTS.accentColor
  state.logoUrl = brandingIconUrl(str(raw.logo_key)) ?? BRANDING_DEFAULTS.logoUrl
  state.faviconUrl = brandingIconUrl(str(raw.favicon_key)) ?? BRANDING_DEFAULTS.faviconUrl
}

/** Raw reactive branding — for code that cannot call a composable. */
export function getBranding() {
  return state
}

export const useBranding = () => {
  return {
    /** e.g. "CityAgent Insights" — the product name shown in the UI. */
    productName: computed(() => state.productName),
    /** e.g. "Your AI analyst for data". */
    tagline: computed(() => state.tagline),
    /** Optional extra footer line; empty string by default. */
    footerText: computed(() => state.footerText),
    /** Hex accent, e.g. "#2563eb". Also published as CSS `--brand-accent`. */
    accentColor: computed(() => state.accentColor),
    /** Logo src — the configured upload, else `/assets/logo-128.png`. */
    logoUrl: computed(() => state.logoUrl),
    /** Favicon href — the configured upload, else `/favicon.ico`. */
    faviconUrl: computed(() => state.faviconUrl),
    /** The raw reactive object (escape hatch). */
    branding: state,
  }
}
