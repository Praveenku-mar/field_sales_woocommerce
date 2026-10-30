<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Products">
      <template #meta>
        <span class="text-ink-gray-5">Synced from the store</span>
      </template>
      <template #actions>
        <Button
          v-if="session.isManager"
          variant="solid"
          label="Sync Products"
          icon-left="lucide-refresh-cw"
          :loading="syncing"
          @click="syncProducts"
        />
      </template>
    </PageHeader>
    <div class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search title, SKU or barcode" :tabs="tabs">
        <template #search-actions>
          <!-- No camera inside Shopify admin (iframe) -->
          <Button v-if="!embedded" icon="lucide-scan-barcode" tooltip="Scan barcode" aria-label="Scan barcode" @click="scanOpen = true" />
        </template>
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        v-model:page-length="pageSize"
        :row-route="(row) => ({ name: 'Product', params: { name: row.name } })"
        empty-icon="lucide-package"
        empty-title="No products found"
        @load-more="list.next()"
      >
        <template #cell-product_title="{ row }">
          <div class="flex items-center gap-3">
            <img v-if="row.image" :src="row.image" class="size-8 shrink-0 rounded-3 border border-desk-border object-cover" />
            <div v-else class="flex size-8 shrink-0 items-center justify-center rounded-3 bg-surface-gray-2">
              <span class="lucide-package size-4 text-ink-gray-4" />
            </div>
            <div class="min-w-0">
              <div class="truncate font-medium text-ink-gray-9">{{ row.product_title }}</div>
              <div class="truncate text-xs text-ink-gray-5">{{ row.variant_title || row.collection || '—' }}</div>
            </div>
          </div>
        </template>
        <template #cell-available_qty="{ row }">
          <span v-if="!row.track_inventory" class="text-ink-gray-4">Not tracked</span>
          <span v-else :class="stockClass(row.available_qty)">{{ formatNumber(row.available_qty) }}</span>
        </template>
        <template #cell-status="{ value }"><StatusPill :status="value === 'Active' ? 'Active' : value" /></template>
      </DataTable>
    </div>
    <BarcodeScanner v-model="scanOpen" @scanned="onScanned" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, call, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import BarcodeScanner from '@/components/BarcodeScanner.vue'
import { isEmbedded } from '@/shopify/embedded'

const embedded = isEmbedded()
import { useDocList } from '@/composables/useDocList'
import { formatCurrency, formatNumber } from '@/utils/format'
import { callMethod, findProductByBarcode } from '@/utils/api'
import { useSessionStore } from '@/stores/session'

const LOW_STOCK = 5
const tabs = [
  { label: 'Active', value: 'Active' },
  { label: 'Low / Out of Stock', value: 'low' },
  { label: 'All', value: 'all' },
]
const columns = [
  { key: 'product_title', label: 'Product' },
  { key: 'sku', label: 'SKU', format: (v) => v || '—' },
  { key: 'price', label: 'Price', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'available_qty', label: 'Stock', align: 'right' },
  { key: 'status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (m.tab === 'Active') filters.push(['status', '=', 'Active'])
  if (m.tab === 'low') {
    filters.push(['status', '=', 'Active'])
    filters.push(['track_inventory', '=', 1])
    filters.push(['available_qty', '<=', LOW_STOCK])
  }
  return filters
}

const route = useRoute()
const { model, list, count, refresh, presetLabel, pageSize } = useDocList({
  doctype: 'Platform Product',
  fields: ['name', 'product_title', 'variant_title', 'collection', 'sku', 'price', 'available_qty', 'track_inventory', 'status', 'image'],
  orderBy: 'product_title asc',
  buildFilters,
  searchFields: ['product_title', 'variant_title', 'sku', 'barcode'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'Active' },
  preset: route.query.preset || null,
})

// ---------------------------------------------------------------- barcode scan

const router = useRouter()
const scanOpen = ref(false)
async function onScanned(code) {
  try {
    const product = await findProductByBarcode(code)
    if (product) router.push({ name: 'Product', params: { name: product.name } })
    else toast.warning(`No product with barcode ${code}`)
  } catch {
    toast.error('Could not look up the barcode. Try again.')
  }
}

function stockClass(qty) {
  return qty <= 0 ? 'font-medium text-ink-red-5' : qty <= LOW_STOCK ? 'text-ink-amber-7' : 'text-ink-green-7'
}

// ---------------------------------------------------------------- sync

// Same as the desk list's Sync Products (managers only, enforced on the server too).
// The app has no realtime connection, so the run's own "Product Sync" Sync Log is
// polled to tell when it has finished.
const session = useSessionStore()
const syncing = ref(false)
const POLL_MS = 4000
const POLL_LIMIT_MS = 25 * 60 * 1000 // the job's own timeout
let pollTimer = null

// The newest "Product Sync" log, or null.
async function lastSyncLog() {
  const [log] = await call('frappe.client.get_list', {
    doctype: 'Sync Log',
    fields: ['name', 'status', 'response_payload', 'error_message', 'creation'],
    filters: { operation: 'Product Sync' },
    order_by: 'creation desc',
    limit_page_length: 1,
  })
  return log || null
}

async function syncProducts() {
  syncing.value = true
  try {
    // The run is done when a log newer than this one appears (both are server times).
    const before = await lastSyncLog()
    const result = await callMethod('field_sales_woocommerce.api.enqueue_product_sync')
    toast[result.queued ? 'info' : 'warning'](result.message)
    waitForSync(before?.creation || null, Date.now())
  } catch {
    // callMethod already toasted the error (e.g. no Default Location).
    syncing.value = false
  }
}

function waitForSync(after, pollStart) {
  pollTimer = setTimeout(async () => {
    try {
      const log = await lastSyncLog()
      if (log && (!after || log.creation > after)) return finishSync(log)
    } catch {
      // A failed poll is retried on the next tick.
    }
    if (Date.now() - pollStart < POLL_LIMIT_MS) waitForSync(after, pollStart)
    else syncing.value = false
  }, POLL_MS)
}

function finishSync(log) {
  syncing.value = false
  if (log.status === 'Success') {
    const s = JSON.parse(log.response_payload || '{}')
    toast.success(`Product sync done: ${s.created ?? 0} created, ${s.updated ?? 0} updated, ${s.unchanged ?? 0} unchanged, ${s.archived ?? 0} archived`)
  } else {
    toast.error(`Product sync failed: ${log.error_message || 'see Sync Log'}`)
  }
  refresh()
}

onBeforeUnmount(() => clearTimeout(pollTimer))
</script>
