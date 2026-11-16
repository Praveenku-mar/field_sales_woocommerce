<template>
  <div>
    <PageHeader title="Status">
      <template #meta>
        <span class="text-ink-gray-5">{{ total }} record{{ total === 1 ? '' : 's' }} not in the store</span>
      </template>
      <template #actions>
        <Button label="Refresh" icon-left="lucide-refresh-cw" @click="refresh" />
      </template>
    </PageHeader>
    <StatusTabs />

    <div class="space-y-6 px-5 pb-6 pt-4">
      <section class="flex flex-col gap-2">
        <h2 class="text-[1.3em] font-semibold text-ink-gray-8">{{ `Field Orders (${orders.count.data ?? '…'})` }}</h2>
        <DataTable
          class="max-h-[60vh]"
          :columns="orderColumns"
          :rows="orders.list.data || []"
          :loading="orders.list.list.loading"
          :total="orders.count.data"
          :has-more="orders.list.hasNextPage"
          v-model:page-length="orders.pageSize.value"
          :row-route="(r) => ({ name: 'Order', params: { name: r.name } })"
          empty-icon="lucide-circle-check"
          empty-title="No failed orders"
          @load-more="orders.list.next()"
        >
          <template #cell-retailer_name="{ row }">
            <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
            <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
          </template>
          <template #cell-sync_error="{ value }"><span class="text-ink-red-5">{{ value || 'Sync failed' }}</span></template>
          <template #row-actions="{ row }">
            <WpRowActions :actions="rowActions('order', row)" />
          </template>
        </DataTable>
      </section>

      <section class="flex flex-col gap-2">
        <h2 class="text-[1.3em] font-semibold text-ink-gray-8">{{ `Shops (${retailers.count.data ?? '…'})` }}</h2>
        <DataTable
          class="max-h-[60vh]"
          :columns="retailerColumns"
          :rows="retailers.list.data || []"
          :loading="retailers.list.list.loading"
          :total="retailers.count.data"
          :has-more="retailers.list.hasNextPage"
          v-model:page-length="retailers.pageSize.value"
          :row-route="(r) => ({ name: 'Retailer', params: { name: r.name } })"
          empty-icon="lucide-circle-check"
          empty-title="No failed shops"
          @load-more="retailers.list.next()"
        >
          <template #cell-retailer_name="{ row }">
            <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
            <div class="text-xs text-ink-gray-5">{{ [row.name, row.city].filter(Boolean).join(' · ') }}</div>
          </template>
          <template #cell-sync_error="{ value }"><span class="text-ink-red-5">{{ value || 'Sync failed' }}</span></template>
          <template #row-actions="{ row }">
            <WpRowActions :actions="rowActions('retailer', row)" />
          </template>
        </DataTable>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { Button, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import StatusTabs from '@/components/StatusTabs.vue'
import WpRowActions from '@/components/wp/WpRowActions.vue'
import DataTable from '@/components/DataTable.vue'
import { useDocList } from '@/composables/useDocList'
import { callMethod } from '@/utils/api'
import { formatDateTime } from '@/utils/format'

// The same presets the dashboard's "Failed Syncs" card counts.
const orders = useDocList({
  doctype: 'Field Order',
  fields: ['name', 'retailer_name', 'sync_error', 'sync_attempts', 'last_sync_attempt'],
  orderBy: 'last_sync_attempt desc',
  buildFilters: () => [],
  preset: 'failed_orders',
})
const retailers = useDocList({
  doctype: 'Retailer',
  fields: ['name', 'retailer_name', 'city', 'sync_error', 'sync_attempts', 'last_sync_attempt'],
  orderBy: 'last_sync_attempt desc',
  buildFilters: () => [],
  preset: 'failed_retailers',
})
const total = computed(() => (orders.count.data || 0) + (retailers.count.data || 0))

const common = [
  { key: 'sync_error', label: 'Error', class: 'max-w-md whitespace-normal' },
  { key: 'sync_attempts', label: 'Attempts', align: 'right', format: (v) => v || 0 },
  { key: 'last_sync_attempt', label: 'Last Attempt', format: (v) => (v ? formatDateTime(v) : '—') },
]
const orderColumns = [{ key: 'retailer_name', label: 'Order' }, ...common]
const retailerColumns = [{ key: 'retailer_name', label: 'Shop' }, ...common]

function refresh() {
  orders.refresh()
  retailers.refresh()
}

// wp-admin row actions under each title: View | Retry sync.
function rowActions(kind, row) {
  return [
    { label: 'View', route: { name: kind === 'order' ? 'Order' : 'Retailer', params: { name: row.name } } },
    { label: retrying[row.name] ? 'Retrying…' : 'Retry sync', disabled: !!retrying[row.name], onClick: () => retry(kind, row.name) },
  ]
}

// Same server methods as the desk's Retry Sync / Sync to Store buttons.
const retrying = reactive({})
async function retry(kind, name) {
  retrying[name] = true
  try {
    const r =
      kind === 'order'
        ? await callMethod('field_sales_woocommerce.api.retry_sync', { field_order: name })
        : await callMethod('field_sales_woocommerce.api.sync_retailers', { names: [name] })
    toast.success(r.message)
    setTimeout(refresh, 4000)
  } finally {
    retrying[name] = false
  }
}
</script>
