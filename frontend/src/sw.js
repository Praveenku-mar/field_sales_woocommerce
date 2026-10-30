import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching'
import { clientsClaim } from 'workbox-core'

// Registered at /assets/field_sales/frontend/sw.js, so its scope is
// /assets/field_sales/frontend/ (same as Frappe HR). That is enough to precache the
// built JS/CSS/fonts/icons and to make the app installable; it does not control /fieldsales.
//
// Only the build output is precached. The HTML is rendered by Frappe per user with boot
// data (csrf_token, session), and /api responses are live data, so neither is cached.
precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()

// autoUpdate: a new worker activates right away and takes over open pages.
self.skipWaiting()
clientsClaim()
