import { isEmbedded } from '@/shopify/embedded'

// Small per-browser preferences (sidebar state, page sizes). Inside the Shopify admin the
// app is a third-party iframe, where storage may be blocked or partitioned (Chrome
// incognito), so values only live for the page there; elsewhere localStorage, if allowed.
const memory = new Map()

export function getPreference(key) {
  if (isEmbedded()) return memory.has(key) ? memory.get(key) : null
  try {
    return localStorage.getItem(key)
  } catch {
    return memory.has(key) ? memory.get(key) : null
  }
}

export function setPreference(key, value) {
  memory.set(key, String(value))
  if (isEmbedded()) return
  try {
    localStorage.setItem(key, String(value))
  } catch {
    // Not remembered across reloads; the app still works.
  }
}
