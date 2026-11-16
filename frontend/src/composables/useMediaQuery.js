import { onBeforeUnmount, ref } from 'vue'

/** A ref that follows a CSS media query, e.g. useMediaQuery('(min-width: 1024px)'). */
export function useMediaQuery(query) {
  const mql = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query) : null
  const matches = ref(!!mql?.matches)
  const update = (e) => (matches.value = e.matches)
  mql?.addEventListener('change', update)
  onBeforeUnmount(() => mql?.removeEventListener('change', update))
  return matches
}
