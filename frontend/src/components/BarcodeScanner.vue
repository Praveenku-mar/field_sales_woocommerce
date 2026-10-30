<template>
  <Dialog v-model="open" title="Scan barcode" size="md" @after-leave="error = ''">
    <div class="space-y-3">
      <div v-if="error" class="flex flex-col items-center gap-2 rounded-4 bg-surface-gray-2 px-4 py-8 text-center">
        <span class="lucide-camera-off size-6 text-ink-gray-5" />
        <p class="text-base font-medium text-ink-gray-9">{{ error.title }}</p>
        <p class="text-sm text-ink-gray-6">{{ error.message }}</p>
        <Button class="mt-2" label="Try again" icon-left="lucide-rotate-ccw" @click="start" />
      </div>
      <div v-show="!error" class="relative overflow-hidden rounded-4 bg-black">
        <div :id="elementId" class="w-full" />
        <div v-if="starting" class="absolute inset-0 flex items-center justify-center">
          <LoadingIndicator class="size-6 text-ink-gray-4" />
        </div>
      </div>
      <p v-if="!error" class="text-center text-sm text-ink-gray-5">Point the rear camera at the product's barcode.</p>
    </div>
  </Dialog>
</template>

<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Button, Dialog, LoadingIndicator } from 'frappe-ui'
import { Html5Qrcode, Html5QrcodeSupportedFormats as F } from 'html5-qrcode'

// Camera barcode scanner: opens the rear camera while the dialog is open and emits
// "scanned" with the first code read, then closes. The camera is always released on close.
const open = defineModel({ type: Boolean, default: false })
const emit = defineEmits(['scanned'])

const FORMATS = [F.EAN_13, F.EAN_8, F.UPC_A, F.UPC_E, F.CODE_128]
const elementId = `fs-barcode-${Math.random().toString(36).slice(2, 10)}`
const starting = ref(false)
const error = ref('')
let scanner = null

watch(open, (isOpen) => (isOpen ? start() : stop()))
onBeforeUnmount(stop)

// The dialog's content is teleported; wait until the scanner's element is in the DOM.
async function waitForElement() {
  for (let i = 0; i < 20; i++) {
    await nextTick()
    if (document.getElementById(elementId)) return true
    await new Promise((r) => setTimeout(r, 25))
  }
  return false
}

async function start() {
  error.value = ''
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    error.value = {
      title: 'Camera not available',
      message: 'The camera only works over HTTPS (or on localhost). Open the app from a secure address.',
    }
    return
  }
  starting.value = true
  try {
    await stop()
    if (!(await waitForElement()) || !open.value) return
    scanner = new Html5Qrcode(elementId, { formatsToSupport: FORMATS, verbose: false, useBarCodeDetectorIfSupported: true })
    await scanner.start(
      { facingMode: 'environment' },
      { fps: 10, qrbox: (w, h) => ({ width: Math.floor(w * 0.85), height: Math.floor(Math.min(h, w) * 0.5) }) },
      onDecoded,
      () => {}, // no code in this frame
    )
    // Closed while the camera was starting.
    if (!open.value) await stop()
  } catch (e) {
    await stop()
    error.value = describeError(e)
  } finally {
    starting.value = false
  }
}

async function onDecoded(text) {
  const code = (text || '').trim()
  if (!code || !scanner) return
  await stop()
  emit('scanned', code)
  open.value = false
}

async function stop() {
  const s = scanner
  scanner = null
  if (!s) return
  try {
    if (s.isScanning) await s.stop()
    s.clear()
  } catch {
    // Already stopped.
  }
}

// html5-qrcode rejects with a DOMException, an Error or a plain string.
function describeError(e) {
  const text = `${e?.name || ''} ${e?.message || e || ''}`
  if (/NotAllowed|Permission|denied/i.test(text)) {
    return {
      title: 'Camera permission denied',
      message: 'Allow camera access for this site in your browser settings, then try again.',
    }
  }
  if (/NotFound|DevicesNotFound|OverconstrainedError|no camera|not found/i.test(text)) {
    return { title: 'No camera found', message: 'This device has no camera the browser can use.' }
  }
  if (/NotReadable|TrackStart|in use/i.test(text)) {
    return { title: 'Camera is busy', message: 'Another app is using the camera. Close it and try again.' }
  }
  return { title: 'Could not start the camera', message: String(e?.message || e || 'Unknown error') }
}
</script>
