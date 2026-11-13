import { isTokenSession } from '@/wordpress/embed'

// Small per-browser preferences (sidebar state, page sizes). Inside a store admin (Shopify or
// wp-admin) the app is a third-party iframe, where storage may be blocked or partitioned
// (Chrome incognito), so values only live for the page there; elsewhere localStorage, if allowed.
const memory = new Map()

export function getPreference(key) {
  if (isTokenSession()) return memory.has(key) ? memory.get(key) : null
  try {
    return localStorage.getItem(key)
  } catch {
    return memory.has(key) ? memory.get(key) : null
  }
}

export function setPreference(key, value) {
  memory.set(key, String(value))
  if (isTokenSession()) return
  try {
    localStorage.setItem(key, String(value))
  } catch {
    // Not remembered across reloads; the app still works.
  }
}
