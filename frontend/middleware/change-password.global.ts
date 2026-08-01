/**
 * Force a signed-in user to choose their own password when a super admin set
 * one for them.
 *
 * ★Named `change-password` rather than `password-change` so it sorts BEFORE
 * `onboarding.global.ts`. Nuxt runs global middleware in filename order, and
 * onboarding's first act is `ensureOrganization()` — which the backend refuses
 * with 403 while a change is pending, so a later-sorting guard would watch the
 * app fail before it ever redirected.
 *
 * The real enforcement is server-side (`_enforce_password_change` in
 * app/core/auth.py refuses every path but this flow). This only spares the
 * person a wall of failed requests.
 */
export default defineNuxtRouteMiddleware((to) => {
  const { data: currentUser, status } = useAuth()
  if (status.value !== 'authenticated') return

  if (!(currentUser.value as any)?.must_change_password) return

  // The change screen itself, and the way out.
  if (to.path === '/users/change-password' || to.path === '/users/sign-in') return

  return navigateTo('/users/change-password')
})
