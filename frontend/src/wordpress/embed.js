// Agamsale inside wp-admin (wordpress-plugin/field-sales.php). www/fieldsales.py sets
// `window.wp_embedded` when a guest opens the page in the plugin's frame
// (field_sales_woocommerce/wordpress_embed.py: page_context).
//
// That frame is usually on another site than wp-admin, where browsers block Frappe's cookies.
// Instead, the plugin signs a short-lived token for the logged-in WordPress user and hands it
// over by postMessage; every API request carries it as `Authorization: Bearer`, which the
// server's auth hook (wordpress_embed.authenticate) turns into the Agamsale user with the same
// email. Location and camera stay available: the plugin's iframe allows them.
import { isEmbedded } from '@/shopify/embedded'

const REFRESH_BEFORE_MS = 60_000
const ANSWER_TIMEOUT_MS = 15_000

/** True when the page runs in the WordPress plugin's frame and signs in with its tokens. */
export function isWpEmbedded() {
  return typeof window !== 'undefined' && window.wp_embedded === true && window.parent !== window
}

/** Inside a store admin's frame (Shopify or WordPress): no Frappe cookie, no login page. */
export function isTokenSession() {
  return isEmbedded() || isWpEmbedded()
}

let current = null // { token, expiresAt }
let pending = null
let seq = 0

/** Ask the plugin page (the parent window) for a new token. */
function requestToken() {
  return new Promise((resolve, reject) => {
    const id = `${Date.now()}-${++seq}`
    const timer = setTimeout(() => {
      cleanup()
      reject(new Error('WordPress did not answer the sign-in request. Reload this page in wp-admin.'))
    }, ANSWER_TIMEOUT_MS)
    function onMessage(event) {
      const msg = event.data
      // Only the plugin page (frame-ancestors limits it to the store) answers; the token
      // itself is checked by the server.
      if (event.source !== window.parent || msg?.type !== 'agamsale:token' || msg.id !== id) return
      cleanup()
      if (msg.token) {
        // Timed by this browser's clock, so a skewed clock does not matter here.
        resolve({ token: msg.token, expiresAt: Date.now() + (Number(msg.expires_in) || 300) * 1000 })
      } else {
        reject(new Error(msg.error || 'WordPress did not issue a sign-in token.'))
      }
    }
    function cleanup() {
      clearTimeout(timer)
      window.removeEventListener('message', onMessage)
    }
    window.addEventListener('message', onMessage)
    // No secret in the request; the answer goes back to this frame's origin only.
    window.parent.postMessage({ type: 'agamsale:token-request', id }, '*')
  })
}

/** A token valid for at least another minute; `fresh` forces a new one (after a 401). */
export function getToken({ fresh = false } = {}) {
  if (!fresh && current && current.expiresAt - Date.now() > REFRESH_BEFORE_MS) {
    return Promise.resolve(current.token)
  }
  pending ||= requestToken()
    .then((next) => {
      current = next
      return next.token
    })
    .finally(() => {
      pending = null
    })
  return pending
}

/** Requests that sign in with the token: this site's API, not assets or other sites. */
function needsToken(url) {
  try {
    const parsed = new URL(url, window.location.href)
    return parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/')
  } catch {
    return false
  }
}

/**
 * Sign every API request in with the plugin's token: fetch (frappe-ui's requests) and
 * XMLHttpRequest (frappe-ui's FileUploader). A 401 on fetch is retried once with a new token.
 */
export function installWpAuth() {
  const nativeFetch = window.fetch.bind(window)
  window.fetch = async (input, init = {}) => {
    const url = input instanceof Request ? input.url : String(input)
    if (!needsToken(url)) return nativeFetch(input, init)
    const send = async (fresh) => {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined))
      headers.set('Authorization', `Bearer ${await getToken({ fresh })}`)
      return nativeFetch(input, { ...init, headers })
    }
    const response = await send(false)
    return response.status === 401 ? send(true) : response
  }

  const { open, send } = XMLHttpRequest.prototype
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._agamsaleToken = needsToken(String(url))
    return open.call(this, method, url, ...rest)
  }
  XMLHttpRequest.prototype.send = function (body) {
    if (!this._agamsaleToken) return send.call(this, body)
    getToken()
      .then(
        (token) => this.setRequestHeader('Authorization', `Bearer ${token}`),
        () => {}, // sent without: the server answers 401 and the uploader shows its error
      )
      .finally(() => {
        try {
          send.call(this, body)
        } catch {
          // Aborted while waiting for the token.
        }
      })
  }
}
