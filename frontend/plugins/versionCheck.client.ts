// New-build detector: an open SPA tab never re-downloads code on its own, so
// after a redeploy users keep running the old bundle until they reload. This
// plugin polls the server's version and, when it changes, shows a persistent
// "new version" toast whose action reloads the page (fresh index.html →
// fresh hashed chunks; /_nuxt assets are immutable so nothing else is needed).
export default defineNuxtPlugin(() => {
  const POLL_MS = 60_000
  let baseline: string | null = null
  let prompted = false

  async function currentServerVersion(): Promise<string | null> {
    try {
      const res = await $fetch<any>('/api/changelog', { timeout: 10_000 } as any)
      return res?.current_version || null
    } catch {
      return null // offline/deploying — try again next tick, never nag on errors
    }
  }

  async function tick() {
    if (prompted) return
    const v = await currentServerVersion()
    if (!v) return
    if (baseline === null) { baseline = v; return }
    if (v === baseline) return
    prompted = true
    try {
      useToast().add({
        title: 'A new version is available',
        description: `v${v} was just deployed — reload to get the latest.`,
        icon: 'i-heroicons-arrow-path',
        timeout: 0, // stays until acted on
        actions: [{
          label: 'Reload now',
          click: () => window.location.reload(),
        }],
      })
    } catch {
      // Toast system unavailable (very early nav) — just reload on next focus.
      window.addEventListener('focus', () => window.location.reload(), { once: true })
    }
  }

  tick()
  setInterval(tick, POLL_MS)
  // Also check immediately when the user returns to the tab — the common
  // "left it open overnight, deploy happened" case.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') tick()
  })
})
