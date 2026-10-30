<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Field Orders">
      <template #actions>
        <Button label="New Order" icon-left="lucide-plus" variant="solid" @click="newOrderOpen = true" />
      </template>
    </PageHeader>
    <div class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search order or retailer" :tabs="tabs" :fields="filterFields">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        v-model:page-length="pageSize"
        :row-route="(row) => ({ name: 'Order', params: { name: row.name } })"
        empty-icon="lucide-shopping-cart"
        empty-title="No orders found"
        @load-more="list.next()"
      >
        <template #cell-name="{ row }">
          <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
          <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
        </template>
        <template #cell-sync_status="{ row }">
          <Tooltip :text="row.sync_error" :disabled="!row.sync_error">
            <StatusPill :status="orderStatus(row)" />
          </Tooltip>
        </template>
        <template #cell-payment_status="{ row }">
          <StatusPill v-if="row.docstatus === 1 && row.payment_status" :status="row.payment_status" />
          <span v-else class="text-ink-gray-4">—</span>
        </template>
      </DataTable>
    </div>
    <NewOrderDialog v-model="newOrderOpen" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Tooltip } from 'frappe-ui'
import NewOrderDialog from '@/components/NewOrderDialog.vue'

const newOrderOpen = ref(false)
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useDocList } from '@/composables/useDocList'
import { useSellLocations } from '@/composables/useSellLocations'
import { formatCurrency, formatDate, formatNumber, orderStatus } from '@/utils/format'

const tabs = [
  { label: 'All', value: 'all' },
  { label: 'Drafts', value: 'draft' },
  { label: 'Synced', value: 'synced' },
  { label: 'Pending', value: 'pending' },
  { label: 'Failed', value: 'failed' },
]
const columns = [
  { key: 'name', label: 'Order' },
  { key: 'sales_rep_name', label: 'Sales Rep' },
  { key: 'location_name', label: 'Location' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'total_qty', label: 'Units', align: 'right', format: (v) => formatNumber(v) },
  { key: 'grand_total', label: 'Total', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'sync_status', label: 'Status' },
  { key: 'payment_status', label: 'Payment' },
]

function buildFilters(m) {
  const filters = []
  const byTab = {
    draft: [['docstatus', '=', 0]],
    synced: [['docstatus', '=', 1], ['sync_status', '=', 'Synced']],
    pending: [['docstatus', '=', 1], ['sync_status', '=', 'Pending']],
    failed: [['docstatus', '=', 1], ['sync_status', '=', 'Failed']],
  }
  if (m.location_id) filters.push(['location_id', '=', m.location_id])
  // Payment state only exists on submitted orders.
  if (m.payment_status) filters.push(['docstatus', '=', 1], ['payment_status', '=', m.payment_status])
  filters.push(...(byTab[m.tab] || [['docstatus', '!=', 2]]))
  return filters
}

const PAYMENT_STATUSES = ['Unpaid', 'Link Sent', 'Partially Paid', 'Paid', 'Link Expired', 'Link Cancelled']
const paymentFilter = {
  fieldname: 'payment_status',
  label: 'Payment',
  options: [{ label: 'All payments', value: '' }, ...PAYMENT_STATUSES.map((v) => ({ label: v, value: v }))],
}

// A location filter only helps when the shop sells from more than one.
const sellLocations = useSellLocations()
const filterFields = computed(() => [
  ...(sellLocations.multiple.value
    ? [{ fieldname: 'location_id', label: 'Location', options: [{ label: 'All locations', value: '' }, ...sellLocations.options.value] }]
    : []),
  paymentFilter,
])

const route = useRoute()
const { model, list, count, presetLabel, pageSize } = useDocList({
  doctype: 'Field Order',
  fields: ['name', 'retailer_name', 'sales_rep', 'sales_rep.full_name as sales_rep_name', 'order_date', 'location_id', 'location_name', 'total_qty', 'grand_total', 'docstatus', 'sync_status', 'sync_error', 'payment_status'],
  orderBy: 'order_date desc, creation desc',
  buildFilters,
  searchFields: ['retailer_name', 'name'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'all' },
  preset: route.query.preset || null,
})
</script>
