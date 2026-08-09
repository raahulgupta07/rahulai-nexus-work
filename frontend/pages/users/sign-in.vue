<template>
  <div v-if="pageLoaded" class="cw-root">

    <!-- top brand bar -->
    <header class="cw-header">
      <div class="cw-brand">
        <img :src="logoUrl" :alt="productName" class="cw-mark" />
        <div class="cw-brand-text">
          <div class="cw-name">{{ brandHead }} <span v-if="brandTail">{{ brandTail }}</span></div>
          <div class="cw-tagline">YOUR AI COWORKER FOR DATA</div>
        </div>
      </div>
      <div class="cw-vchip"><span class="cw-vdot"></span><span>{{ envLabel }}</span></div>
    </header>

    <!-- body -->
    <main class="cw-main">

      <!-- LEFT: sign in -->
      <div class="cw-left">
        <section class="cw-form-col">
          <div v-if="needsSetup" class="cw-setupbadge">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M12 2l8 4v6c0 5-3.5 8-8 10-4.5-2-8-5-8-10V6l8-4z" stroke="#1D4ED8" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 12l2 2 4-4" stroke="#1D4ED8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            FIRST-RUN SETUP
          </div>
          <h1 v-if="needsSetup" class="cw-h1">Welcome,<br>create your <span>super-admin</span></h1>
          <h1 v-else class="cw-h1">{{ greeting }},<br>sign in to <span>{{ productName }}</span></h1>
          <p v-if="needsSetup" class="cw-sub">This is the first account on this server — it becomes the owner and manages everything. Sign-up locks automatically once it's created.</p>
          <p v-else class="cw-sub">Your AI analyst for data — answered with the source query, every time.</p>

          <p v-if="error_message" v-html="error_message" class="cw-error"></p>

          <form @submit.prevent="needsSetup ? createAdmin() : signInWithCredentials()" v-if="needsSetup || authMode !== 'sso_only' || localOverride || ldapMode" class="cw-form">
            <label v-if="needsSetup" class="cw-field">
              <span class="cw-lab">FULL NAME</span>
              <input id="adminName" type="text" v-model="adminName" placeholder="Admin" autocomplete="name" class="cw-in" />
            </label>
            <!-- ★Label and placeholder swap with the door the user picked. A directory
                 account is a username, not an address, and nobody should have to know
                 the word "LDAP" to sign in — they just use the credential they were given. -->
            <label class="cw-field">
              <span class="cw-lab">{{ ldapMode ? $t('auth.usernameFieldLabel') : $t('auth.emailFieldLabel') }}</span>
              <input id="email" type="text" v-model="email" :placeholder="ldapMode ? $t('auth.ldapUsernamePlaceholder') : 'you@company.com'" autocomplete="username" class="cw-in" />
            </label>
            <label class="cw-field">
              <span class="cw-lab">PASSWORD</span>
              <input id="password" :type="showPw ? 'text' : 'password'" v-model="password" placeholder="••••••••••" autocomplete="current-password" class="cw-in cw-in-pw" />
              <button type="button" class="cw-show" @click="showPw = !showPw">{{ showPw ? 'Hide' : 'Show' }}</button>
            </label>

            <div v-if="needsSetup" class="cw-pwhint">
              <span><b>✓</b> 8+ characters</span><span><b>✓</b> the only account that can invite others</span>
            </div>

            <!-- ★"Remember me" is a local-session choice; the directory door does not
                 offer it, so it hides rather than sitting there doing nothing. -->
            <div class="cw-row" v-if="!needsSetup && !ldapMode">
              <button type="button" class="cw-rem" @click="rememberMe = !rememberMe">
                <span class="cw-ck" :class="{ on: rememberMe }">
                  <svg v-if="rememberMe" width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.2l2.2 2.3L9.5 3.5" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </span>
                <span>Remember me</span>
              </button>
              <NuxtLink v-if="smtpEnabled" to="/users/forgot-password" class="cw-link">{{ $t('auth.forgotPassword') }}</NuxtLink>
            </div>

            <button type="submit" :disabled="isSubmitting" class="cw-btn cw-btn-primary">
              <template v-if="isSubmitting">
                <Spinner class="h-5 w-5" />
                {{ needsSetup ? 'Creating admin…' : $t('auth.loggingIn') }}
              </template>
              <template v-else>
                {{ needsSetup ? 'Create admin & sign in' : (ldapMode ? $t('auth.signInWithLdap') : 'Continue with email') }}
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M5 12h13M13 6l6 6-6 6" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </template>
            </button>
          </form>

          <template v-if="needsSetup">
            <p class="cw-locknote">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><rect x="5" y="11" width="14" height="9" rx="2" stroke="#94A3B8" stroke-width="1.8"/><path d="M8 11V8a4 4 0 0 1 8 0v3" stroke="#94A3B8" stroke-width="1.8"/></svg>
              This screen appears only once — registration closes after this account.
            </p>
            <div class="cw-nextbox">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" style="flex-shrink:0;margin-top:1px"><circle cx="12" cy="12" r="9" stroke="#1D4ED8" stroke-width="1.8"/><path d="M12 8h.01M11 12h1v4h1" stroke="#1D4ED8" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <span>After sign-in: add your <b>OpenRouter key</b> in <b>Settings → Models</b>, then invite your team from <b>Settings → Members</b>.</span>
            </div>
          </template>

          <template v-if="!needsSetup && authMode !== 'local_only' && (googleSignIn || oidcProviders.length || ldapEnabled)">
            <div class="cw-or" v-if="authMode === 'hybrid'">
              <span class="cw-or-line"></span>
              <span class="cw-or-text">OR CONTINUE WITH</span>
              <span class="cw-or-line"></span>
            </div>

            <div class="cw-provs">
              <!-- ★The directory door goes FIRST and never redirects — it flips the form
                   above between the two credential kinds. Google/OIDC stay visible in
                   both modes: they are still valid ways in. -->
              <button v-if="ldapEnabled" type="button" class="cw-btn cw-prov" @click="toggleLdapMode">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" class="cw-provico"><rect x="3" y="4" width="18" height="6" rx="2" stroke="#2563EB" stroke-width="1.7"/><rect x="3" y="14" width="18" height="6" rx="2" stroke="#2563EB" stroke-width="1.7"/><path d="M7 7h.01M7 17h.01" stroke="#2563EB" stroke-width="2" stroke-linecap="round"/></svg>
                {{ ldapMode ? $t('auth.continueWithEmail') : $t('auth.continueWithLdap') }}
              </button>

              <button v-if="googleSignIn" type="button" class="cw-btn cw-prov" @click="signInWithGoogle" :disabled="loadingProvider !== null">
                <Spinner v-if="loadingProvider === 'google'" class="h-4 w-4" />
                <img v-else src="/llm_providers_icons/google-icon.png" alt="" class="cw-provico" />
                {{ loadingProvider === 'google' ? $t('auth.redirecting') : 'Continue with Google' }}
              </button>

              <button v-for="p in oidcProviders" :key="p.name" type="button" class="cw-btn cw-prov cw-prov-tint"
                @click="() => signInWithProvider(p)" :disabled="loadingProvider !== null">
                <Spinner v-if="loadingProvider === p.name" class="h-4 w-4" />
                <!-- ★The provider's own mark, chosen by an admin in Settings.
                     This used to be one hardcoded shield drawn for EVERY
                     provider, so Google, Entra and a self-hosted Keycloak were
                     visually identical here and the Logo picker had no effect on
                     the one screen it names. ProviderMark falls back to the
                     letter tile when a provider has no mark set. -->
                <SettingsProviderMark v-else :icon="p.icon" :label="p.label || p.name" :size="18" class="cw-provico" />
                {{ loadingProvider === p.name ? $t('auth.redirecting') : `Continue with ${p.label || p.name}` }}
              </button>
            </div>
          </template>
        </section>
      </div>

      <!-- RIGHT: live agent showcase -->
      <AuthShowcase class="cw-right" />
    </main>

    <footer class="cw-foot">© 2026 {{ productName }} · {{ footerText || tagline }}</footer>
  </div>
  <div v-else class="flex h-screen items-center justify-center" style="background:#F8FAFC"><Spinner class="h-6 w-6" /></div>
</template>


<script setup lang="ts">

import qs from 'qs';

import { ref, computed, onMounted } from 'vue';
import Spinner from '~/components/Spinner.vue';
import AuthShowcase from '~/components/auth/AuthShowcase.vue';

const { t } = useI18n()
// Branding comes from the PUBLIC /api/settings feed (fetched once by
// plugins/settings.ts), so it resolves on this pre-login screen too.
const { productName, tagline, footerText, logoUrl } = useBranding()
// The brand mark colours the last word of the product name (default:
// "CityAgent <span>Insights</span>"). Split on the final space so any
// configured name keeps that treatment; a single-word name just renders plain.
const brandHead = computed(() => {
  const parts = productName.value.trim().split(/\s+/)
  return parts.length > 1 ? parts.slice(0, -1).join(' ') : productName.value
})
const brandTail = computed(() => {
  const parts = productName.value.trim().split(/\s+/)
  return parts.length > 1 ? parts[parts.length - 1] : ''
})
const { rawToken } = useAuthState()
const { fetchOrganization } = useOrganization()
const route = useRoute()
const config = useRuntimeConfig();
const googleSignIn = ref(config.public.googleSignIn);
// configured=false means the provider is enabled but its admin hasn't finished
// setup — the button still shows but clicking explains it isn't ready yet.
const googleConfigured = ref(true)
const oidcProviders = ref<{ name: string; enabled: boolean; label: string; icon?: string | null; configured: boolean }[]>([])
const loadingProvider = ref<string | null>(null)
const authMode = ref<'hybrid'|'local_only'|'sso_only'>('hybrid')
const smtpEnabled = ref(false)
// LDAP is a second door on the SAME form, not a redirect. Fail-closed: a feed
// without an `ldap` block (older backend) leaves the page exactly as it was.
const ldapEnabled = ref(false)
const ldapMode = ref(false)
const isSubmitting = ref(false)
const showPw = ref(false)
const rememberMe = ref(true)
// First-run: server has zero users -> show the create-super-admin form.
// Flips false forever once the first account exists. Fail-closed (default false).
const needsSetup = ref(false)
const adminName = ref('Admin')
const localOverride = computed(() => route.query.local === 'true')

// Time-of-day greeting (client-only SPA, local hour is correct).
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 5) return 'Working late'
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  if (h < 22) return 'Good evening'
  return 'Working late'
})

const envLabel = computed(() => {
  if (import.meta.client) {
    const h = window.location.hostname
    if (h === 'localhost' || h === '127.0.0.1') return 'local'
  }
  return 'live'
})

definePageMeta({
  auth: {
    unauthenticatedOnly: true,
  },
    layout: 'users'
})

// Define reactive references for email and password
const email = ref('');
const password = ref('');

const error_message = ref('')
// Extract the signIn function from useAuth
const { signIn, getSession } = useAuth();

// Only honor redirects to same-origin paths to avoid open-redirect bugs
function safeRedirectTarget(value: unknown): string | null {
  if (typeof value !== 'string' || !value) return null
  if (!value.startsWith('/') || value.startsWith('//')) return null
  return value
}

const OAUTH_REDIRECT_STORAGE_NAME = 'bow:postSignInRedirect'

// Helper to extract error message from server response
function extractErrorMessage(error: any, fallback: string): string {
  const data = error?.data
  if (!data) return fallback

  // Handle FastAPI validation errors (detail array)
  if (Array.isArray(data.detail)) {
    return data.detail.map((d: any) => d.msg || d.message || JSON.stringify(d)).join('\n')
  }
  // Handle simple detail string
  if (typeof data.detail === 'string') {
    return data.detail
  }
  // Handle message field
  if (data.message) {
    return data.message
  }
  return fallback
}
const pageLoaded = ref(false)

// Add this code to handle URL parameters
onMounted(async () => {
  try {
    const settings = await $fetch('/api/settings')
    if (settings?.oidc_providers?.length) {
      oidcProviders.value = settings.oidc_providers
        .filter((p: any) => p.enabled)
        .map((p: any) => ({
          name: p.name,
          enabled: !!p.enabled,
          label: p.label || p.name,
          // ★Copy the mark. This map rebuilds each provider field by field, so
          // anything not named here is silently dropped — `icon` was, and the
          // button fell back to a letter tile while the feed carried the real
          // value. A whitelist map is the quietest place in a page for a new
          // field to disappear.
          icon: p.icon || null,
          configured: p.configured !== false,
        }))
    }
    // Drive the Google button from the live feed so an enabled-but-unconfigured
    // Google entry also surfaces (with the friendly not-ready message).
    const g = (settings as any)?.google_oauth
    if (g) {
      googleSignIn.value = !!g.enabled
      googleConfigured.value = g.configured !== false
    }
    if (settings?.auth?.mode) {
      authMode.value = settings.auth.mode
    }
    smtpEnabled.value = settings?.smtp_enabled ?? false
    ldapEnabled.value = !!(settings as any)?.ldap?.enabled
    needsSetup.value = !!(settings as any)?.needs_setup
  } catch (_) {}
  const inviteError = route.query.error as string
  if (inviteError) {
    error_message.value = inviteError
  }
  const access_token = route.query.access_token as string
  const userEmail = route.query.email as string
  if (access_token) {
    rawToken.value = access_token
    await getSession({ force: true })
    // Check if the user has an organization (same as credentials login)
    const org = await fetchOrganization()
    if (!org || !org.id) {
      navigateTo('/organizations/new')
    } else {
      let pendingRedirect: string | null = null
      try {
        pendingRedirect = safeRedirectTarget(sessionStorage.getItem(OAUTH_REDIRECT_STORAGE_NAME))
        sessionStorage.removeItem(OAUTH_REDIRECT_STORAGE_NAME)
      } catch (_) {}
      navigateTo(pendingRedirect || '/')
    }
    return
  }
  pageLoaded.value = true
})


function persistRedirectForOAuth() {
  const target = safeRedirectTarget(route.query.redirect)
  try {
    if (target) {
      sessionStorage.setItem(OAUTH_REDIRECT_STORAGE_NAME, target)
    } else {
      sessionStorage.removeItem(OAUTH_REDIRECT_STORAGE_NAME)
    }
  } catch (_) {}
}

// First-run: create the super-admin then sign in with the same credentials.
// Uses the public register endpoint — its first-user path auto-elevates the
// account to superuser + org owner (auth.py _ensure_org_for_first_uninvited_user).
// After it exists, needs_setup is false on the next load, so this is one-shot.
async function createAdmin() {
  isSubmitting.value = true
  error_message.value = ''
  try {
    await $fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: email.value,
        password: password.value,
        name: adminName.value || 'Admin',
      }),
    })
  } catch (error: any) {
    error_message.value = extractErrorMessage(error, 'Could not create the admin account.')
    isSubmitting.value = false
    return
  }
  needsSetup.value = false
  await signInWithCredentials()
}

// Switching doors clears the previous door's error — it was about the other
// credential and reads as a rejection of the one now being typed.
function toggleLdapMode() {
  ldapMode.value = !ldapMode.value
  error_message.value = ''
}

async function signInWithCredentials() {
  isSubmitting.value = true
  error_message.value = ''
  const route = useRoute();
  const redirectedFrom = safeRedirectTarget(route.query.redirect)

  const credentials = {
    username: email.value,
    password: password.value,
  };

  try {
    // Same form-encoded shape and same JWT response either way — only the door differs.
    const response = await $fetch(ldapMode.value ? '/api/auth/ldap/login' : '/api/auth/jwt/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: qs.stringify(credentials),
    });


    if (response) {
      rawToken.value = response.access_token
      await getSession({ force: true })

      // Check if the user has an organization
      const org = await fetchOrganization();
      if (!org || !org.id) {
        navigateTo('/organizations/new');
      } else {
        if (redirectedFrom) {
          navigateTo(redirectedFrom);
        } else {
          navigateTo('/');
        }
      }
    }
    else {
      error_message.value = t('auth.invalidCredentials')
      isSubmitting.value = false
    }
  } catch (error: any) {
    error_message.value = loginErrorMessage(error)
    isSubmitting.value = false
  }
}

// ★The sign-in form is deliberately vague about WHY a password was rejected,
// and specific about account state — because those two are not the same risk.
//
// A wrong address and a wrong password both come back as LOGIN_BAD_CREDENTIALS,
// and they must keep reading identically here. Splitting them turns this form
// into an account-existence oracle: submit a list of addresses, keep the ones
// that say "wrong password", and you have a verified staff roster harvested
// without a single valid credential.
//
// ACCOUNT_DEACTIVATED and EMAIL_NOT_VERIFIED are different: the backend only
// sends them once the password has ALREADY been verified, so naming the state
// tells the caller nothing they could not confirm themselves. That asymmetry
// is the whole design — silent before the password checks out, specific after.
// See UserManager.authenticate in backend/app/core/auth.py.
function loginErrorMessage(error: any): string {
  const detail = error?.data?.detail ?? error?.response?._data?.detail ?? ''
  const code = typeof detail === 'string' ? detail : ''
  if (code === 'ACCOUNT_DEACTIVATED') return t('auth.accountDeactivated')
  if (code === 'EMAIL_NOT_VERIFIED') return t('auth.emailNotVerified')
  // Everything else, including LOGIN_BAD_CREDENTIALS, gets the one message.
  return t('auth.invalidCredentials')
}

// Add new function for Google sign-in
async function signInWithGoogle() {
  // Enabled but not finished — don't redirect into a broken flow.
  if (!googleConfigured.value) {
    error_message.value = 'Google sign-in is not available yet — ask your admin to finish setup.'
    return
  }
  try {
    loadingProvider.value = 'google'
    persistRedirectForOAuth()
    const response = await $fetch('/api/auth/google/authorize', {
      method: 'GET',
    });

    if (response.authorization_url) {
      window.location.href = response.authorization_url;
    }
  } catch (error) {
    error_message.value = t('auth.googleInitError')
    loadingProvider.value = null
  }
}

async function signInWithProvider(p: { name: string; label?: string; configured?: boolean }) {
  // Enabled but not finished — don't redirect into a broken flow.
  if (p.configured === false) {
    error_message.value = `${p.label || p.name} sign-in is not available yet — ask your admin to finish setup.`
    return
  }
  const name = p.name
  try {
    loadingProvider.value = name
    persistRedirectForOAuth()
    const response = await $fetch(`/api/auth/${name}/authorize`, { method: 'GET' })
    if ((response as any)?.authorization_url) {
      window.location.href = (response as any).authorization_url
    }
  } catch (error) {
    error_message.value = t('auth.providerInitError', { provider: name })
    loadingProvider.value = null
  }
}
</script>

<style scoped>
/* Accent: --brand-accent / --brand-accent-hover / --brand-accent-rgb are set on
   <html> at runtime by plugins/branding.client.ts. Every use keeps the original
   literal as the var() fallback, so an unconfigured instance renders exactly as
   before (and so does the pre-hydration paint). */
.cw-root{
  min-height:100vh;width:100%;position:relative;color:#111827;display:flex;flex-direction:column;
  background:radial-gradient(60% 42% at 50% 118%, rgba(37,99,235,.14), transparent 70%),linear-gradient(0deg,#F8FAFC,#FFFFFF);
  font-family:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased;
}
.cw-root::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.55;
  background-image:linear-gradient(#eef1f6 1px,transparent 1px),linear-gradient(90deg,#eef1f6 1px,transparent 1px);
  background-size:32px 32px;-webkit-mask-image:linear-gradient(180deg,#000,transparent 80%)}
.cw-header,.cw-main,.cw-foot{position:relative;z-index:1}

.cw-header{display:flex;align-items:center;justify-content:space-between;padding:16px 44px;max-width:1500px;margin:0 auto;width:100%;flex-shrink:0}
.cw-brand{display:flex;align-items:center;gap:12px}
.cw-mark{width:40px;height:40px;object-fit:contain;display:block;border-radius:9px}
.cw-name{font-size:16px;font-weight:700;letter-spacing:-.01em;color:#111827;line-height:1.1}
.cw-name span{color:var(--brand-accent, #2563EB)}
.cw-tagline{font-size:9.5px;letter-spacing:.22em;color:#94A3B8;font-weight:600;margin-top:2px}
.cw-vchip{display:flex;align-items:center;gap:7px;padding:6px 12px;border:1px solid #E5E7EB;border-radius:999px;background:#FFF;font-size:12px;color:#4B5563;font-weight:600}
.cw-vdot{width:7px;height:7px;border-radius:50%;background:#22C55E;box-shadow:0 0 0 3px rgba(34,197,94,.18)}

.cw-main{flex:1;min-height:0;display:grid;grid-template-columns:1.02fr 1fr;gap:44px;max-width:1500px;margin:0 auto;width:100%;padding:0 44px 16px;align-items:center}
.cw-left{display:flex;flex-direction:column;justify-content:center;padding-left:56px;min-height:0}
.cw-form-col{max-width:440px;width:100%}

.cw-h1{font-weight:600;font-size:40px;line-height:1.12;letter-spacing:-.02em;margin:0 0 12px;color:#0F172A}
.cw-h1 span{color:var(--brand-accent, #2563EB)}
.cw-sub{margin:0 0 22px;font-size:15px;line-height:1.5;color:#6B7280;max-width:390px}
.cw-error{margin:0 0 16px;color:#DC2626;font-size:13.5px;white-space:pre-line}

.cw-form{display:flex;flex-direction:column;gap:10px}
.cw-field{display:flex;flex-direction:column;gap:3px;background:#FFF;border:1px solid #E5E7EB;border-radius:12px;padding:9px 15px;position:relative;transition:border-color .16s, box-shadow .16s}
.cw-field:focus-within{border-color:var(--brand-accent, #2563EB);box-shadow:0 0 0 4px rgba(var(--brand-accent-rgb, 37,99,235),.12)}
.cw-lab{font-size:11px;font-weight:600;letter-spacing:.03em;color:#9CA3AF}
.cw-in{border:none;outline:none;background:transparent;font-family:inherit;font-size:15px;color:#111827;padding:1px 0}
.cw-in-pw{padding-right:52px}
.cw-show{position:absolute;right:13px;top:50%;transform:translateY(-50%);border:none;background:#F1F5F9;color:#64748B;font-family:inherit;font-size:12px;font-weight:600;padding:5px 11px;border-radius:8px;cursor:pointer}

.cw-row{display:flex;align-items:center;justify-content:space-between;padding:1px 2px 3px}
.cw-rem{display:flex;align-items:center;gap:9px;border:none;background:none;cursor:pointer;font-family:inherit;padding:0;font-size:13.5px;color:#475569}
.cw-ck{width:18px;height:18px;border-radius:6px;display:flex;align-items:center;justify-content:center;background:#FFF;border:1.5px solid #CBD5E1;transition:.15s}
.cw-ck.on{background:var(--brand-accent, #2563EB);border-color:var(--brand-accent, #2563EB)}
.cw-link{font-size:13.5px;color:#64748B;text-decoration:none;font-weight:500}
.cw-link:hover{color:var(--brand-accent, #2563EB)}

.cw-btn{width:100%;min-height:48px;display:flex;align-items:center;justify-content:center;gap:10px;border-radius:11px;font-family:inherit;font-size:14.5px;font-weight:600;padding:0 16px;cursor:pointer;transition:border-color .15s, background .15s, box-shadow .15s, transform .08s}
.cw-btn:disabled{opacity:.55;cursor:not-allowed;transform:none}
.cw-btn-primary{border:1px solid var(--brand-accent, #2563EB);background:var(--brand-accent, #2563EB);color:#fff;font-size:15px;box-shadow:0 12px 24px -12px rgba(var(--brand-accent-rgb, 37,99,235),.6)}
.cw-btn-primary:hover{background:var(--brand-accent-hover, #1D4ED8);border-color:var(--brand-accent-hover, #1D4ED8);transform:translateY(-1px)}
.cw-btn-primary:active{transform:translateY(0)}

.cw-or{display:flex;align-items:center;gap:14px;margin:16px 0 12px}
.cw-or-line{flex:1;height:1px;background:#E5E7EB}
.cw-or-text{font-size:11.5px;font-weight:600;letter-spacing:.06em;color:#94A3B8}

.cw-provs{display:flex;flex-direction:column;gap:10px}
.cw-prov{border:1px solid #E5E7EB;background:#FFF;color:#1F2937}
.cw-prov:hover{border-color:#CBD5E1;background:#F8FAFC;transform:translateY(-1px)}
.cw-prov-tint{background:#F5F8FF;border-color:#DCE7FB}
.cw-prov-tint:hover{border-color:var(--brand-accent, #2563EB);color:var(--brand-accent-hover, #1D4ED8);background:#EFF6FF}
.cw-provico{width:17px;height:17px;flex-shrink:0}

.cw-right{align-self:stretch;min-height:0}

.cw-foot{text-align:center;padding:8px 0 14px;font-size:12.5px;color:#94A3B8;flex-shrink:0}

/* first-run setup */
.cw-setupbadge{display:inline-flex;align-items:center;gap:7px;margin-bottom:14px;padding:5px 12px 5px 9px;border:1px solid #DCE7FB;border-radius:999px;background:#F5F8FF;font-size:11.5px;font-weight:700;letter-spacing:.05em;color:#1D4ED8}
.cw-pwhint{display:flex;gap:14px;padding:1px 2px 4px;font-size:12.5px;color:#64748B}
.cw-pwhint b{color:#16A34A;font-weight:700}
.cw-locknote{margin:14px 0 0;font-size:12.5px;color:#94A3B8;display:flex;align-items:center;gap:8px}
.cw-nextbox{margin-top:13px;display:flex;gap:10px;align-items:flex-start;padding:11px 14px;background:#F5F8FF;border:1px solid #DCE7FB;border-radius:13px;font-size:12.5px;line-height:1.5;color:#475569}
.cw-nextbox b{color:#1D4ED8}

@media (max-width:1024px){
  .cw-main{grid-template-columns:1fr}
  .cw-right{display:none}
  .cw-header,.cw-main{padding-left:24px;padding-right:24px}
  .cw-left{padding-left:0}
  .cw-h1{font-size:32px}
}
</style>
