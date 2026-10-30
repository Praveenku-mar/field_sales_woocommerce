<template>
  <div>
    <PageHeader title="Sync Failures">
      <template #meta>
        <span class="text-ink-gray-5">{{ total }} record{{ total === 1 ? '' : 's' }} not in the store</span>
      </template>
      <template #actions>
        <Button icon="lucide-refresh-cw" variant="ghost" @click="refresh" />
      </template>
    </PageHeader>

    <div class="space-y-5 px-5 pb-6">
      <SectionCard :title="`Field Orders (${orders.count.data ?? '…'})`" flush>
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
          <template #cell-actions="{ row }">
            <Button label="Retry" size="sm" :loading="retrying[row.name]" @click.stop="retry('order', row.name)" />
          </template>
        </DataTable>
      </SectionCard>

      <SectionCard :title="`Customers (${retailers.count.data ?? '…'})`" flush>
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
          empty-title="No failed customers"
          @load-more="retailers.list.next()"
        >
          <template #cell-retailer_name="{ row }">
            <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
            <div class="text-xs text-ink-gray-5">{{ [row.name, row.city].filter(Boolean).join(' · ') }}</div>
          </template>
          <template #cell-sync_error="{ value }"><span class="text-ink-red-5">{{ value || 'Sync failed' }}</span></template>
          <template #cell-actions="{ row }">
            <Button label="Retry" size="sm" :loading="retrying[row.name]" @click.stop="retry('retailer', row.name)" />
          </template>
        </DataTable>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { Button, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
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
  { key: 'actions', label: '', align: 'right' },
]
const orderColumns = [{ key: 'retailer_name', label: 'Order' }, ...common]
const retailerColumns = [{ key: 'retailer_name', label: 'Customer' }, ...common]

function refresh() {
  orders.refresh()
  retailers.refresh()
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
