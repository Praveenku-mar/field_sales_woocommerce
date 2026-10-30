import fs from 'fs'
import path from 'path'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import frappeui from 'frappe-ui/vite'
import { VitePWA } from 'vite-plugin-pwa'

// `yarn dev` serves the app on :8080 and proxies /api, /app, /login, /assets,
// /files to the bench web server (:8000, read from common_site_config.json).
// Open it as http://<site>:8080/fieldsales so requests reach the right site.
// If 8080 is taken, run with FS_DEV_PORT=8081 (the proxy plugin ignores --port).
// Vite rejects unknown Host headers. Allow this bench's site names (sites/*/site_config.json),
// which is what the dev server is opened as, and nothing else.
const sitesDir = path.resolve(import.meta.dirname, '../../../sites')
const benchSites = fs.existsSync(sitesDir)
  ? fs.readdirSync(sitesDir).filter((name) => fs.existsSync(path.join(sitesDir, name, 'site_config.json')))
  : []

export default defineConfig({
  plugins: [
    // index.html's Jinja block (App Bridge for the Shopify admin) only means something once
    // Frappe renders the built page; the dev server would serve it as text.
    {
      name: 'strip-embedded-head-in-dev',
      apply: 'serve',
      transformIndexHtml: (html) => html.replace(/\{% if boot\.embedded %\}[\s\S]*?\{% endif %\}\s*/, ''),
    },
    frappeui({
      frappeProxy: { port: Number(process.env.FS_DEV_PORT) || undefined },
      jinjaBootData: true,
      // Its esbuild helper breaks Vite 8's dependency scan; we use no code editor.
      codeLanguages: false,
      buildConfig: {
        outDir: '../field_sales_woocommerce/public/frontend',
        indexHtmlPath: '../field_sales_woocommerce/www/fieldsales.html',
        emptyOutDir: true,
      },
    }),
    vue({
      // App Bridge web components (<s-app-nav>, <s-link>; src/shopify/EmbeddedNav.vue).
      template: { compilerOptions: { isCustomElement: (tag) => tag.startsWith('s-') } },
    }),
    // Installable PWA. The service worker (src/sw.js) only precaches the built assets;
    // the HTML is rendered by Frappe with boot data and is never cached.
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.js',
      registerType: 'autoUpdate',
      injectRegister: null,
      // main.js registers the worker (injectRegister: null); the manifest link is in index.html.
      manifestFilename: 'manifest.webmanifest',
      injectManifest: {
        globPatterns: ['**/*.{js,css,woff2,svg,png}'],
      },
      manifest: {
        name: 'Field Sales',
        short_name: 'Field Sales',
        description: 'Visits, orders and stock for field sales reps',
        start_url: '/fieldsales',
        scope: '/fieldsales',
        id: '/fieldsales',
        display: 'standalone',
        background_color: '#ffffff',
        theme_color: '#ffffff',
        icons: [
          { src: '/assets/field_sales/manifest/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/assets/field_sales/manifest/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/assets/field_sales/manifest/maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: '/assets/field_sales/manifest/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    allowedHosts: benchSites,
  },
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
    dedupe: ['vue', 'vue-router', 'frappe-ui', 'reka-ui', '@vueuse/core'],
  },
  optimizeDeps: {
    include: ['tailwind.config.js'],
    // frappe-ui ships source; pre-bundling it would load parts of it twice.
    exclude: ['frappe-ui'],
  },
})
