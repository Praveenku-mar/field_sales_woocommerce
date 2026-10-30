import './index.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { FrappeUI, setConfig, frappeRequest, toast } from 'frappe-ui'

import App from './App.vue'
import router from './router'
import './composables/useInstallPrompt' // listens for beforeinstallprompt from page load
import { isEmbedded, useAdminToasts, withSessionToken } from './shopify/embedded'

// createResource / createListResource / createDocumentResource all fetch through this.
// Inside the Shopify admin there is no Frappe cookie: each request carries a session token.
setConfig('resourceFetcher', isEmbedded() ? withSessionToken(frappeRequest) : frappeRequest)
if (isEmbedded()) useAdminToasts(toast)
// frappe-ui sends the browser's hostname as X-Frappe-Site-Name, which names no site
// behind a tunnel or proxy (ngrok, a custom domain). Send the site that served the page.
setConfig('requestHeaders', () =>
  window.site_name ? { 'X-Frappe-Site-Name': window.site_name } : {},
)

const app = createApp(App)
app.use(FrappeUI)
app.use(createPinia())

async function start() {
  if (import.meta.env.DEV) {
    // In production, www/fieldsales.py renders these into the page (jinjaBootData).
    const boot = await frappeRequest({
      url: '/api/method/field_sales_woocommerce.www.fieldsales.get_context_for_dev',
      method: 'GET',
    })
    Object.assign(window, boot)
  }
  // Installing the router starts the first navigation, whose guard calls the API:
  // only do it once the boot values (csrf_token) are on window.
  app.use(router)
  app.mount('#app')
  registerServiceWorker()
}

// Precaches the built assets and makes the app installable (see src/sw.js).
// Not inside the Shopify admin: the app is not installable there.
function registerServiceWorker() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator) || isEmbedded()) return
  // Only an update replaces an existing controller; the first install does not.
  const hadController = !!navigator.serviceWorker.controller
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) toast.success('App updated')
  })
  navigator.serviceWorker.register('/assets/field_sales/frontend/sw.js').catch((error) => {
    console.error('Service worker registration failed', error)
  })
}

start()
