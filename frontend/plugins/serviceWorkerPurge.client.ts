// Service-worker kill switch.
//
// This app has never registered a service worker and does not want one. But a
// worker registered by whatever ran on the same origin BEFORE this app — an
// earlier product, a demo, a static site — keeps running until something
// unregisters it, and while it lives it intercepts every request and answers
// from its own cache. The result is an installation that looks permanently
// stuck on an old build: the server is updated, a fresh browser shows the new
// UI, and the affected browser never does.
//
// ★ A hard-refresh does NOT fix this. Cmd/Ctrl+Shift+R bypasses the HTTP
// cache, not a controlling worker — the worker still handles the fetch. That
// is why this cannot be left to "tell them to hard-refresh"; the only remedies
// are DevTools > Application > Unregister, or code like this.
//
// ★ Scope is per-ORIGIN (scheme + host + port). A worker on citygpt.xyz cannot
// control insights.citygpt.xyz. This only ever cleans up its own origin.
//
// Safe as a no-op: on an origin that never had a worker, getRegistrations()
// returns an empty list and nothing happens.
export default defineNuxtPlugin(() => {
  // The whole API is secure-context only, and `caches` is absent over plain
  // http on a non-localhost host — which is exactly how someone might first
  // reach a new deployment. Missing API means no worker can exist either.
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return

  // Guards a reload loop. If a worker somehow reappears (a stray script, a
  // browser extension), we clean it up but reload at most once per tab.
  const RELOADED_KEY = 'cityagent.swPurge.reloaded'

  const alreadyReloaded = (): boolean => {
    try { return sessionStorage.getItem(RELOADED_KEY) === '1' } catch { return true }
  }
  const markReloaded = () => {
    try { sessionStorage.setItem(RELOADED_KEY, '1') } catch { /* private mode */ }
  }

  // Cache Storage is only ever populated by a service worker, and this app has
  // none — so anything here belongs to a foreign worker and is safe to drop.
  //
  // ★ Deliberately NOT nested inside the "found a registration" branch. A
  // worker that is being unregistered stays active until its last client goes
  // away, and it will happily re-create its cache while serving the reload we
  // just triggered. On the next load there is no registration left to find, so
  // a purge gated on registrations returns early and the entry survives
  // forever. Sweeping unconditionally is what actually finishes the job.
  const dropForeignCaches = async (): Promise<number> => {
    if (typeof caches === 'undefined') return 0
    try {
      const keys = await caches.keys()
      if (!keys.length) return 0
      await Promise.all(keys.map(k => caches.delete(k).catch(() => false)))
      return keys.length
    } catch {
      return 0 // Cache Storage unavailable — unregistering still helps
    }
  }

  const purge = async () => {
    const registrations = await navigator.serviceWorker.getRegistrations()

    if (!registrations.length) {
      const dropped = await dropForeignCaches()
      if (dropped) {
        console.warn(`[CityAgent] Cleared ${dropped} orphaned cache store(s) left by a removed service worker.`)
      }
      return
    }

    // Whether THIS page was served by a worker. If it was, its HTML may be
    // stale and a reload is the point of the exercise. If it was not, the
    // worker is merely installed and unregistering is enough on its own.
    const wasControlled = !!navigator.serviceWorker.controller

    const results = await Promise.all(
      registrations.map(r => r.unregister().catch(() => false)),
    )
    const removed = results.filter(Boolean).length
    if (!removed) return

    await dropForeignCaches()

    console.warn(
      `[CityAgent] Removed ${removed} stale service worker registration(s) from this origin. ` +
      (wasControlled ? 'Reloading to fetch the current build.' : 'No reload needed.'),
    )

    if (wasControlled && !alreadyReloaded()) {
      markReloaded()
      window.location.reload()
    }
  }

  // Never let cleanup break the app: any failure here is strictly less bad
  // than the stale-bundle problem it exists to fix.
  purge().catch(() => { /* ignore */ })
})
