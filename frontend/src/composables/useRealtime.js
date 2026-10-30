import { onBeforeUnmount } from 'vue'
import { io } from 'socket.io-client'
import { isEmbedded } from '@/shopify/embedded'

// One socket.io connection to Frappe's realtime server, opened on first use and
// shared by every page. Same host as the page; on a dev server (bench start / yarn dev)
// the socket server listens on its own port (socketio_port), in production nginx
// proxies /socket.io on the page's own origin. Signed in through the sid cookie.
// Inside the Shopify admin there is no sid cookie for the socket to sign in with: no socket
// there. Document events are polled instead (POLL_MS); doctype-wide events are skipped (their
// payloads can't be rebuilt by polling; the server re-checks prices and stock on save/submit).
const POLL_MS = 30000

let socket = null
const subscribedDoctypes = new Set()

function getSocket() {
  if (socket) return socket
  let host = window.location.origin
  if (window.dev_server) host = `${window.location.protocol}//${window.location.hostname}:${window.socketio_port || 9000}`
  socket = io(`${host}/${window.site_name}`, {
    withCredentials: true,
    reconnectionAttempts: 5,
    extraHeaders: window.site_name ? { 'X-Frappe-Site-Name': window.site_name } : {},
  })
  // Rooms are per connection: join them again after a reconnect.
  socket.on('connect', () => subscribedDoctypes.forEach((d) => socket.emit('doctype_subscribe', d)))
  socket.on('connect_error', (err) => console.warn('Realtime connection failed:', err.message))
  return socket
}

/**
 * Listen for `event` published to `doctype`'s room (server: publish_realtime(event, ..., doctype=...))
 * while the calling component is mounted.
 */
export function onDoctypeEvent(doctype, event, handler) {
  if (isEmbedded()) return
  const s = getSocket()
  if (!subscribedDoctypes.has(doctype)) {
    subscribedDoctypes.add(doctype)
    if (s.connected) s.emit('doctype_subscribe', doctype)
  }
  s.on(event, handler)
  onBeforeUnmount(() => s.off(event, handler))
}

/**
 * Listen for `event` about one document (server: publish_realtime(event, ..., doctype=, docname=))
 * while the calling component is mounted. Joins the document's room; the server checks read access.
 * The handler only gets messages whose `name` is `docname`.
 */
export function onDocEvent(doctype, docname, event, handler) {
  if (isEmbedded()) {
    // Polled: the handler gets `{ name, polled: true }` instead of the event's payload.
    const timer = setInterval(() => handler({ name: docname, polled: true }), POLL_MS)
    onBeforeUnmount(() => clearInterval(timer))
    return
  }
  const s = getSocket()
  const join = () => s.emit('doc_subscribe', doctype, docname)
  if (s.connected) join()
  s.on('connect', join)
  const listener = (data) => data?.name === docname && handler(data)
  s.on(event, listener)
  onBeforeUnmount(() => {
    s.off(event, listener)
    s.off('connect', join)
    s.emit('doc_unsubscribe', doctype, docname)
  })
}
