import { defineNuxtConfig } from "nuxt/config"

export default defineNuxtConfig({
  devtools: { enabled: true },
  ssr: false,
  app: {
    head: {
      title: 'CityAgent Insights',
      titleTemplate: '%s · CityAgent Insights',
    },
  },

  modules: [
    "@nuxt/ui",
    "@sidebase/nuxt-auth",
    'nuxt-tiptap-editor',
    '@nuxtjs/mdc',
    '@nuxt-alt/proxy',
    'nuxt-3-intercom',
    'nuxt-echarts',
    'nuxt-monaco-editor'
  ],

  echarts: {
    charts: [
      'BarChart',
      'LineChart',
      'PieChart',
      'ScatterChart',
      'EffectScatterChart',
      'BoxplotChart',
      'CandlestickChart',
      'GaugeChart',
      'FunnelChart',
      'HeatmapChart',
      'LinesChart',
      'MapChart',
      'ParallelChart',
      'RadarChart',
      'SunburstChart',
      'TreeChart',
      'TreemapChart'
    ],
    components: [
      'AriaComponent',
      'AxisPointerComponent',
      'BrushComponent',
      'CalendarComponent',
      'DataZoomComponent',
      'DataZoomInsideComponent',
      'DataZoomSliderComponent',
      'DatasetComponent',
      'GridComponent',
      'LegendComponent',
      'MarkLineComponent',
      'MarkPointComponent',
      'ParallelComponent',
      'RadarComponent'
    ]
  },

  intercom: {
    appId: 'ocwih86k',
    autoBoot: false
  },

  tiptap: {
    prefix: 'Tiptap'
  },

  plugins: [
    '~/plugins/vue-draggable-resizable.client.js',
    '~/plugins/vue-flow.client.js',
    '~/plugins/i18n.ts',
  ],

  css: [
    '~/assets/css/rtl.css',
    '~/assets/css/transitions.css',
    '~/assets/css/mobile.css',
  ],

  imports: {
    dirs: ['ee/composables'],
    presets: [
      { from: 'vue-i18n', imports: ['useI18n'] },
    ],
  },

  icon: {
    localApiEndpoint: '/_nuxt_icon',
    serverBundle: {
      collections: ['heroicons'],
    },
    clientBundle: {
      scan: true,
    },
    fallbackToApi: false,
  },

  colorMode: {
    preference: 'system'
  },

  proxy: {
    debug: true,
    experimental: {
        listener: true
    },
    proxies: {
        '/.well-known': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => path
        },
        '/mcp': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => `/api${path}`
        },
        '/swagger': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => path
        },
        '/openapi.json': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => path
        },
        '/excel': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => `/api${path}`
        },
        '/api': {
            target: process.env.NUXT_DEV_PROXY_TARGET || 'http://127.0.0.1:8000',
            changeOrigin: true,
            secure: false,
            rewrite: (path) => path,
            headers: {
                'Connection': 'keep-alive'
            }
        }
    }
},

  auth: {
    baseURL: '/api/', // Proxy now handled by NGINX
    provider: {
      type: 'local',
      pages: {
        login: '/users/sign-in',
        signup: '/users/sign-up'
      },
      endpoints: {
        signIn: { path: '/auth/jwt/login', method: 'post' },
        signOut: { path: '/auth/jwt/logout', method: 'post' },
        signUp: { path: '/auth/jwt/register', method: 'post' },
        getSession: { path: '/users/whoami', method: 'get' }
      },
      token: {
        signInResponseTokenPointer: '/access_token',
        type: 'Bearer',
        maxAgeInSeconds: 60 * 60 * 24 * 7, // 7 days
        // ★These are the FLAT keys @sidebase/nuxt-auth actually reads. The
        // nested `cookie: { name, options }` block below is accepted by the
        // config schema and then silently ignored by the module — measured in
        // the shipped runtime payload, which carried BOTH spellings side by
        // side with `secureCookieAttribute:false` while the nested block said
        // `secure:true`. The intent was right and had no effect for as long as
        // it has existed.
        //
        // `secureCookieAttribute` marks the cookie HTTPS-only. It is opt-IN via
        // DASH_COOKIE_SECURE rather than defaulting to true, because this is a
        // STATIC build: the value is baked at image-build time, and turning it
        // on for an installation served over plain HTTP (this fork's own dev
        // install runs on http://localhost:8095) makes the browser refuse to
        // store the cookie and login silently stops working, with nothing in
        // any log to explain it.
        // ★★★So: any deployment served over HTTPS MUST build with
        // DASH_COOKIE_SECURE=true. Without it the 7-day session cookie travels
        // in clear text on every request.
        cookieName: 'auth.token',
        secureCookieAttribute: process.env.DASH_COOKIE_SECURE === 'true',
        sameSiteAttribute: 'lax',
        // ★httpOnly is deliberately NOT set, and cannot be: this SPA reads the
        // cookie in JavaScript to build the `Authorization: Bearer` header. Any
        // XSS therefore yields a working 7-day token. Making it HttpOnly means
        // moving to a cookie-transport session on the backend — a real change,
        // not a flag. Recorded here so the exposure is a known trade-off rather
        // than an oversight.
        //
        // ★The nested `cookie: { name, options }` block that used to sit here
        // has been DELETED, not corrected. It was never valid configuration —
        // the type checker reports "'cookie' does not exist in type" — so it had
        // no effect at all, while reading as though `secure: true` were set in
        // production. Leaving a corrected copy would only re-create the trap:
        // two spellings, one of them silently ignored.
      },
      sessionDataType: { id: 'integer', name: 'string', email: 'string', is_superuser: 'boolean',
        organizations: '{ name: string, description: string | null, id: string, role: string, roles?: string[], permissions?: string[], resource_permissions?: Record<string, string[]>, is_enterprise?: boolean, usage_quota?: any }[]'
      },
    },
    session: {
      enableRefreshOnWindowFocus: true,
      enableRefreshPeriodically: false
    },
    globalAppMiddleware: {
      isEnabled: true
    },
    rewriteRedirects: true,
    fullPathRedirect: true
  },

  runtimeConfig: {
    public: {
      baseURL: '/api',
      environment: process.env.NODE_ENV,
    }
  },

  nitro: {
    experimental: {
      websocket: false
    },
    // Emit `.br` and `.gz` siblings next to every compressible file in
    // `.output/public`, which the Dockerfile copies wholesale to
    // /app/frontend/dist. FastAPI serves the static bundle itself and was
    // sending it uncompressed: the sign-in page alone cost 4.12 MB, of which
    // the entry chunk is 857 KB (279 KB gzipped) and `entry.css` 216 KB
    // (30 KB gzipped). Compressing at BUILD time rather than per request means
    // brotli can run at maximum quality once instead of costing CPU on every
    // hit. nitropack 2.13.1 supports both encodings
    // (`CompressOptions { gzip, brotli }`); files under 1 KB, `.map` files and
    // already-compressed mime types are skipped by design.
    compressPublicAssets: {
      gzip: true,
      brotli: true
    }
  },

  hooks: {
    // ★The login page was pulling down essentially the whole product before
    // the user had typed a password: 86 `<link rel="prefetch">` tags, ~3.15 MB
    // of reports, dashboards and chart code.
    //
    // They are not NuxtLink's doing and not a route rule. `ssr: false` plus
    // `yarn generate` (Dockerfile:132) prerenders ONE shell — index.html —
    // that serves every route, and vue-bundle-renderer builds its hint list by
    // walking the entry chunk's dynamic imports and marking every one
    // `prefetch` (`vue-bundle-renderer/dist/runtime.mjs:117-134`, gated solely
    // on the manifest entry's own `prefetch` flag). In an SPA every route
    // chunk is a dynamic import of that one entry, so "prefetch what this page
    // needs next" and "prefetch the entire application" are the same list, and
    // there is no per-route HTML in which to say otherwise. Dropping the flag
    // is therefore the narrowest lever that exists here, not a shortcut past a
    // finer one.
    //
    // ★Trade-off, stated plainly: a route whose link is not on screen is
    // fetched on demand at navigation time instead of ahead of it. In-app
    // navigation keeps its prefetching — NuxtLink's runtime prefetch calls the
    // route's own component loader when the link enters the viewport and does
    // not consult this manifest flag, so visible links still warm up.
    // `preload` is untouched, so the entry chunk and its CSS still arrive as
    // modulepreload/stylesheet on first paint.
    'build:manifest'(manifest) {
      for (const entry of Object.values(manifest)) {
        if (entry.prefetch) {
          entry.prefetch = false
        }
      }
    }
  },

  // Allow ngrok domains to access the dev server (for Slack webhooks via frontend proxy)
  vite: {
    server: {
      allowedHosts: [
        '.ngrok-free.app'
      ]
    },
    optimizeDeps: {
      // nuxt-tiptap-editor puts the tiptap packages it registers into
      // build.transpile, which excludes them from Vite's dev pre-bundling —
      // they are served as raw ESM. The app's own tiptap deps
      // (@tiptap/extension-mention, @tiptap/suggestion) were NOT excluded, so
      // Vite pre-bundled them with a second, inlined copy of prosemirror-state.
      // Two prosemirror-state instances run separate auto-key counters, and
      // their unkeyed plugins collide ("RangeError: Adding different instances
      // of a keyed plugin (plugin$…)"), which aborts Editor creation and leaves
      // every instruction editor blank in dev. Exclude the whole
      // tiptap/prosemirror family so dev resolves exactly one copy of each
      // module. Production builds are unaffected (single Rollup graph).
      // CJS deps of excluded packages still need prebundling for ESM interop.
      // Heavy libs (echarts/mermaid/ag-grid/markdown-it/…) are listed so Vite
      // pre-bundles them ONCE at dev startup instead of discovering them lazily
      // on first route hit — which otherwise triggers repeated
      // "optimized dependencies changed. reloading" full-page reloads and makes
      // the first dev session feel very slow. No effect on production builds.
      include: [
        'tiptap-markdown > markdown-it-task-lists',
        'markdown-it',
        '@vueuse/core',
        'diff-match-patch',
        'mermaid',
        'markstream-vue',
        'ag-grid-vue3',
        'echarts',
        'echarts/core',
        'echarts/renderers',
        'echarts/charts',
        'echarts/components',
        'echarts/features',
      ],
      exclude: [
        '@tiptap/extension-mention',
        '@tiptap/suggestion',
        '@tiptap/extension-table',
        '@tiptap/extension-table-row',
        '@tiptap/extension-table-cell',
        '@tiptap/extension-table-header',
        'tiptap-markdown',
        '@tiptap/pm',
        'prosemirror-changeset',
        'prosemirror-collab',
        'prosemirror-commands',
        'prosemirror-dropcursor',
        'prosemirror-gapcursor',
        'prosemirror-history',
        'prosemirror-inputrules',
        'prosemirror-keymap',
        'prosemirror-markdown',
        'prosemirror-menu',
        'prosemirror-model',
        'prosemirror-schema-basic',
        'prosemirror-schema-list',
        'prosemirror-state',
        'prosemirror-tables',
        'prosemirror-trailing-node',
        'prosemirror-transform',
        'prosemirror-view',
      ]
    }
  },

  routeRules: {
    '/data': { redirect: '/agents' },
    '/data/**': { redirect: '/agents/**' },
    // The org-wide evals page is gone — evals live in the Agents explorer, under
    // each agent's Evals group and under Global Evals. Redirected rather than
    // deleted because /evals has been linked from chat transcripts, docs and
    // bookmarks, and a 404 for those is worse than a hop.
    //
    // NOT '/evals/**': the run-detail route stays. A run spans cases that may
    // target different agents, so there is no single agent to nest it under,
    // and every run link already in an old transcript keeps working.
    '/evals': { redirect: '/agents' },
  },

  compatibilityDate: '2025-08-03',
})
