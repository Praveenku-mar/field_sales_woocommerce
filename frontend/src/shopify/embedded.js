// Field Sales inside the Shopify admin (App Bridge). www/fieldsales.py sets `window.embedded`,
// `shop`, `host` and `shopify_api_key` when the page is opened from the admin; the App Bridge
// script (loaded first in <head> in that case) provides the `shopify` global.
//
// There are no Frappe cookies in the admin's iframe: every API call carries a session token
// (`Authorization: Bearer`), which the server's auth hook (field_sales/shopify/session_token.py)
// turns into the embedded user. Nothing here runs for the normal, cookie-signed-in app.

/** True when the page was opened inside the Shopify admin. */
export function isEmbedded() {
  return window.embedded === true
}

/** The App Bridge global, once its script has loaded. */
function appBridge() {
  return typeof window !== 'undefined' ? window.shopify : undefined
}

/**
 * Wrap frappe-ui's resource fetcher so each request carries a fresh session token.
 * App Bridge's fetch interceptor also adds one to same-origin requests; setting it here
 * keeps createResource calls working even if a request bypasses the interceptor.
 */
export function withSessionToken(fetcher) {
  return async (options) => {
    const token = await appBridge()?.idToken?.()
    const headers = token ? { ...(options.headers || {}), Authorization: `Bearer ${token}` } : options.headers
    return fetcher({ ...options, headers })
  }
}

/** Send frappe-ui toasts to the admin's toast (shopify.toast.show) instead of the in-iframe one. */
export function useAdminToasts(toast) {
  const show = (isError) => (message) => {
    const text = typeof message === 'string' ? message : String(message ?? '')
    appBridge()?.toast?.show(text, { isError })
  }
  toast.success = show(false)
  toast.info = show(false)
  toast.warning = show(false)
  toast.error = show(true)
}

/**
 * Open an app path (e.g. "/fieldsales/visits/VIS-0001") in a new browser tab, outside the
 * Shopify admin: the standalone app, where location and camera work (needs its own login).
 */
export function openStandalone(path) {
  window.open(new URL(path, window.location.origin).href, '_blank', 'noopener')
}

/** Boot values shown on the embedded Settings page. */
export function embeddedShop() {
  return window.shop || ''
}
