<template>
  <Dialog v-model="open" title="Select Location" size="lg" @after-leave="teardown">
    <div class="space-y-3">
      <p class="text-sm text-ink-gray-5">Search for the shop, or tap the map / drag the pin to its location.</p>
      <div class="flex gap-2">
        <TextInput
          v-model="query"
          placeholder="Search an address or place"
          class="flex-1"
          @keyup.enter="runSearch"
        >
          <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
        </TextInput>
        <Button label="Search" icon-left="lucide-search" :loading="searching" @click="runSearch" />
      </div>
      <ul
        v-if="results.length"
        class="max-h-32 divide-y divide-outline-gray-1 overflow-y-auto rounded-4 border border-outline-gray-2"
      >
        <li
          v-for="r in results"
          :key="r.place_id"
          class="flex cursor-pointer items-start gap-2 px-3 py-2 text-sm hover:bg-surface-gray-1"
          @click="chooseResult(r)"
        >
          <span class="lucide-map-pin mt-0.5 size-4 shrink-0 text-ink-gray-4" />
          <span class="min-w-0">
            <span class="block truncate font-medium text-ink-gray-9">{{ r.name || r.display_name.split(",")[0] }}</span>
            <span class="block truncate text-xs text-ink-gray-5">{{ r.display_name }}</span>
          </span>
        </li>
      </ul>
      <div ref="mapEl" class="h-96 overflow-hidden rounded-4 border border-desk-border" />
      <div class="flex items-center justify-between text-sm">
        <span class="tabular-nums text-ink-gray-6">{{ picked ? `${picked.latitude}, ${picked.longitude}` : 'No location picked yet' }}</span>
        <a
          v-if="picked"
          :href="osmLink"
          target="_blank"
          rel="noopener"
          class="text-ink-gray-5 hover:underline"
        >Open in OpenStreetMap ↗</a>
      </div>
    </div>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" @click="open = false" />
        <Button variant="solid" label="Use This Location" :disabled="!picked" @click="confirm" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Button, Dialog, TextInput, toast } from 'frappe-ui'
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'

const props = defineProps({
  /** Initial coordinates ({ latitude, longitude } as numbers/strings), if any. */
  latitude: { type: [Number, String], default: null },
  longitude: { type: [Number, String], default: null },
})
const emit = defineEmits(['select'])

const open = defineModel({ type: Boolean, default: false })

// Somewhere sensible when no coordinates exist yet: the sales territory's city.
const FALLBACK = [11.0168, 76.9558]

const mapEl = ref(null)
const picked = ref(null)
let map = null
let marker = null

// Place search (OpenStreetMap's Nominatim; same terms as the map tiles —
// keyless, and only queried on Enter/button, never per keystroke).
const query = ref('')
const results = ref([])
const searching = ref(false)
async function runSearch() {
  const q = query.value.trim()
  if (!q || searching.value) return
  searching.value = true
  results.value = []
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(q)}`,
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    results.value = await res.json()
    if (!results.value.length) toast.info('No places found for that search')
  } catch {
    toast.error('Place search is unavailable right now')
  } finally {
    searching.value = false
  }
}

function chooseResult(r) {
  results.value = []
  const latlng = { lat: Number(r.lat), lng: Number(r.lon) }
  map.setView([latlng.lat, latlng.lng], 18)
  place(latlng)
}

watch(open, async (value) => {
  if (!value) return
  query.value = ''
  results.value = []
  picked.value =
    Number(props.latitude) || Number(props.longitude)
      ? { latitude: Number(Number(props.latitude).toFixed(6)), longitude: Number(Number(props.longitude).toFixed(6)) }
      : null
  await nextTick()
  // Let the dialog's enter animation lay the container out before Leaflet measures it.
  setTimeout(setup, 50)
})

function setup() {
  if (map) {
    resetView()
    return
  }
  map = L.map(mapEl.value, { zoomControl: true, attributionControl: true })
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map)
  map.on('click', (e) => place(e.latlng))
  resetView()
}

function resetView() {
  const center = picked.value ? [picked.value.latitude, picked.value.longitude] : FALLBACK
  map.setView(center, picked.value ? 16 : 12)
  map.invalidateSize()
  if (marker) {
    marker.remove()
    marker = null
  }
  if (picked.value) addMarker(center)
}

function addMarker(latlng) {
  marker = L.marker(latlng, { icon: pinIcon(), draggable: true, autoPan: true })
  marker.on('dragend', () => place(marker.getLatLng()))
  marker.addTo(map)
}

function place(latlng) {
  picked.value = { latitude: Number(latlng.lat.toFixed(6)), longitude: Number(latlng.lng.toFixed(6)) }
  if (marker) {
    marker.setLatLng(latlng)
  } else {
    addMarker([latlng.lat, latlng.lng])
  }
}

function confirm() {
  emit('select', picked.value)
  open.value = false
}

function teardown() {
  // Leaflet keeps DOM/timer hooks outside Vue; drop the map when the dialog closes.
  if (map) {
    map.remove()
    map = null
    marker = null
  }
}

// A CSS pin, not Leaflet's image marker: its image assets do not survive bundlers.
const pinIcon = () =>
  L.divIcon({
    className: '',
    html: '<span class="lucide-map-pin size-6 text-ink-red-5" style="filter: drop-shadow(0 1px 1px rgb(0 0 0 / 0.3));" />',
    iconSize: [24, 24],
    iconAnchor: [12, 22],
  })

const osmLink = computed(() =>
  `https://www.openstreetmap.org/?mlat=${picked.value.latitude}&mlon=${picked.value.longitude}#map=18/${picked.value.latitude}/${picked.value.longitude}`,
)
</script>
