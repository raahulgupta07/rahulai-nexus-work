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
        cookie: {
          name: 'auth_token',
          options: {
            path: '/',
            secure: process.env.NODE_ENV === 'production',
            sameSite: 'lax'
          }
        }
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
  },

  compatibilityDate: '2025-08-03',
})
