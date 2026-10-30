<template>
  <div>
    <PageHeader :title="d.product_title || name" :status="d.status || null">
      <template #meta>
        <span v-if="d.variant_title" class="text-ink-gray-5">{{ d.variant_title }}</span>
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Product not found" :description="errorText" /></div></div>

    <div v-else class="grid grid-cols-1 gap-5 px-5 pb-6 xl:grid-cols-3">
      <div class="space-y-5 xl:col-span-2">
        <SectionCard>
          <div class="flex gap-5">
            <img v-if="d.image" :src="d.image" :alt="d.product_title" class="size-36 shrink-0 rounded-4 border border-desk-border object-cover" />
            <div v-else class="flex size-36 shrink-0 items-center justify-center rounded-4 bg-surface-gray-2">
              <span class="lucide-package size-10 text-ink-gray-4" />
            </div>
            <div class="min-w-0 flex-1 space-y-4">
              <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span class="text-2xl font-semibold tabular-nums text-ink-gray-9">{{ formatCurrency(d.price) }}</span>
                <span v-if="d.compare_at_price > d.price" class="text-ink-gray-5 line-through">{{ formatCurrency(d.compare_at_price) }}</span>
                <span v-if="discount" class="text-sm font-medium text-ink-green-7">{{ discount }}% off</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-ink-gray-5">Stock:</span>
                <span v-if="!d.track_inventory" class="text-ink-gray-7">Not tracked (always available)</span>
                <template v-else>
                  <span class="font-medium tabular-nums" :class="stock.class">{{ formatNumber(d.available_qty) }}</span>
                  <StatusPill :status="stock.status" :label="stock.label" />
                </template>
              </div>
              <InfoGrid :items="details" :columns="3" />
            </div>
          </div>
        </SectionCard>

        <SectionCard v-if="d.track_inventory && locationStock.data?.length" title="Stock by Location" flush>
          <DataTable :columns="locationColumns" :rows="locationStock.data">
            <template #cell-available_qty="{ row }">
              <span :class="stockOf(row).class">{{ formatNumber(row.available_qty) }}</span>
            </template>
          </DataTable>
        </SectionCard>

        <SectionCard v-if="activity.data?.variants?.length" :title="`Other Variants (${activity.data.variants.length})`" flush>
          <DataTable :columns="variantColumns" :rows="activity.data.variants" :row-route="(r) => ({ name: 'Product', params: { name: r.name } })">
            <template #cell-available_qty="{ row }">
              <span v-if="!row.track_inventory" class="text-ink-gray-4">Not tracked</span>
              <span v-else :class="stockOf(row).class">{{ formatNumber(row.available_qty) }}</span>
            </template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Recent Orders" flush>
          <DataTable
            :columns="orderColumns"
            :rows="activity.data?.recent_orders || []"
            :loading="activity.loading"
            :row-route="(r) => ({ name: 'Order', params: { name: r.name } })"
            empty-icon="lucide-shopping-cart"
            empty-title="Not ordered yet"
          >
            <template #cell-sync_status="{ row }"><StatusPill :status="orderStatus(row)" /></template>
          </DataTable>
        </SectionCard>
      </div>

      <div class="space-y-5">
        <SectionCard title="Last 30 Days">
          <div class="grid grid-cols-3 gap-3 text-center">
            <div><div class="text-xl font-semibold tabular-nums">{{ activity.data?.orders_30d ?? '—' }}</div><div class="text-xs text-ink-gray-5">Orders</div></div>
            <div><div class="text-xl font-semibold tabular-nums">{{ activity.data ? formatNumber(activity.data.units_30d) : '—' }}</div><div class="text-xs text-ink-gray-5">Units</div></div>
            <div><div class="truncate text-xl font-semibold tabular-nums">{{ activity.data ? formatCurrency(activity.data.value_30d, undefined, 0) : '—' }}</div><div class="text-xs text-ink-gray-5">Value</div></div>
          </div>
          <p class="mt-3 text-xs text-ink-gray-5">Submitted orders you can see.</p>
        </SectionCard>

        <SectionCard title="Store Link">
          <InfoGrid :items="storeInfo" :columns="1" />
        </SectionCard>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Button, createDocumentResource, createListResource, createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import { formatCurrency, formatDate, formatDateTime, formatNumber, orderStatus } from '@/utils/format'

// Same threshold as the desk's Platform Product list.
const LOW_STOCK = 5

const props = defineProps({ name: { type: String, required: true } })

const doc = createDocumentResource({ doctype: 'Platform Product', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

const activity = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_product_activity',
  params: { product: props.name },
  auto: true,
})

// Stock at each location the shop sells from (the figure above is the default location's).
const locationStock = createListResource({
  doctype: 'Product Stock',
  fields: ['name', 'location_id', 'location_name', 'available_qty', 'stock_updated_at'],
  filters: { product: props.name },
  orderBy: 'location_name asc',
  pageLength: 50,
  auto: true,
})

function stockOf(row) {
  const qty = row.available_qty || 0
  if (qty <= 0) return { status: 'Failed', label: 'Out of Stock', class: 'text-ink-red-5' }
  if (qty <= LOW_STOCK) return { status: 'Pending', label: 'Low Stock', class: 'text-ink-amber-7' }
  return { status: 'Synced', label: 'In Stock', class: 'text-ink-green-7' }
}
const stock = computed(() => stockOf(d.value))

const discount = computed(() =>
  d.value.compare_at_price > d.value.price ? Math.round((1 - d.value.price / d.value.compare_at_price) * 100) : 0,
)

const details = computed(() => [
  { label: 'SKU', value: d.value.sku },
  { label: 'Barcode', value: d.value.barcode },
  { label: 'Collection', value: d.value.collection },
  { label: 'Unit', value: d.value.uom },
  { label: 'Variant', value: d.value.variant_title },
  { label: 'Status', value: d.value.status },
])
const storeInfo = computed(() => [
  { label: 'Platform', value: d.value.platform },
  { label: 'Product ID', value: d.value.external_product_id },
  { label: 'Variant ID', value: d.value.external_variant_id },
  { label: 'Inventory item', value: d.value.external_inventory_id, hide: !d.value.track_inventory },
  { label: 'Stock location', value: d.value.location_id, hide: !d.value.track_inventory },
  { label: 'Last synced', value: d.value.last_synced ? formatDateTime(d.value.last_synced) : null },
])

const variantColumns = [
  { key: 'variant_title', label: 'Variant', format: (v) => v || 'Default' },
  { key: 'sku', label: 'SKU', format: (v) => v || '—' },
  { key: 'price', label: 'Price', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'available_qty', label: 'Stock', align: 'right' },
]
const locationColumns = [
  { key: 'location_name', label: 'Location', format: (v, row) => v || row.location_id },
  { key: 'available_qty', label: 'Available', align: 'right' },
  { key: 'stock_updated_at', label: 'Updated', format: (v) => (v ? formatDateTime(v) : '—') },
]
const orderColumns = [
  { key: 'name', label: 'Order' },
  { key: 'retailer_name', label: 'Customer' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'qty', label: 'Qty', align: 'right', format: (v) => formatNumber(v) },
  { key: 'amount', label: 'Amount', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'sync_status', label: 'Status' },
]
</script>
