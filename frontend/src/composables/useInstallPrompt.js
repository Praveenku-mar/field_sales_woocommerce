import { computed, ref } from 'vue'

// Registered at module load (imported from main.js) so an early beforeinstallprompt is not missed.
const deferredPrompt = ref(null)
const installed = ref(false)

const standalone =
  typeof window !== 'undefined' &&
  (window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true)

const ua = typeof navigator !== 'undefined' ? navigator.userAgent : ''
const isIos =
  /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1)
const isIosSafari = isIos && /safari/i.test(ua) && !/crios|fxios|edgios|opios/i.test(ua)

// Not inside the Shopify admin (embedded; see shopify/embedded.js): no install prompt there.
if (typeof window !== 'undefined' && window.embedded !== true) {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    deferredPrompt.value = event
  })
  window.addEventListener('appinstalled', () => {
    installed.value = true
    deferredPrompt.value = null
  })
}

export function useInstallPrompt() {
  const canInstall = computed(() => !standalone && !installed.value && !!deferredPrompt.value)
  const showIosHint = computed(() => !standalone && !installed.value && isIosSafari)

  async function install() {
    const event = deferredPrompt.value
    if (!event) return
    event.prompt()
    await event.userChoice
    // The event can only be used once.
    deferredPrompt.value = null
  }

  return { canInstall, showIosHint, install }
}
