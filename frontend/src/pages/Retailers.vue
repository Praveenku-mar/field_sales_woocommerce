<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Customers">
      <template #actions>
        <Button
          v-if="session.isManager"
          label="Import from Store"
          icon-left="lucide-download"
          :loading="importing"
          @click="importCustomers"
        />
        <Button label="New Customer" icon-left="lucide-plus" variant="solid" @click="newRetailerOpen = true" />
      </template>
    </PageHeader>
    <div class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search name, city, phone" :tabs="tabs" :fields="fields">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        v-model:page-length="pageSize"
        :row-route="(row) => ({ name: 'Retailer', params: { name: row.name } })"
        empty-icon="lucide-store"
        empty-title="No customers found"
        @load-more="list.next()"
      >
        <template #cell-retailer_name="{ row }">
          <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
          <div class="text-xs text-ink-gray-5">{{ [row.name, row.city].filter(Boolean).join(' · ') }}</div>
        </template>
        <template #cell-last_visit="{ value }">
          <span :class="lastVisitClass(value)">{{ relativeDay(value) }}</span>
        </template>
        <template #cell-sync_status="{ row }">
          <StatusPill :status="row.status === 'Inactive' ? 'Inactive' : row.sync_status || 'Draft'" />
        </template>
      </DataTable>
    </div>
    <RetailerDialog v-model="newRetailerOpen" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { Button, call, toast } from 'frappe-ui'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import RetailerDialog from '@/components/RetailerDialog.vue'
import { useDocList } from '@/composables/useDocList'
import { daysAgo, relativeDay } from '@/utils/format'
import { callMethod } from '@/utils/api'
import { useSessionStore } from '@/stores/session'

const newRetailerOpen = ref(false)

const tabs = [
  { label: 'Active', value: 'Active' },
  { label: 'Inactive', value: 'Inactive' },
  { label: 'All', value: 'all' },
]
const fields = [
  { fieldname: 'retailer_type', label: 'Type', options: ['', 'Kirana', 'Supermarket', 'Wholesaler', 'Medical', 'Other'] },
  { fieldname: 'sync_status', label: 'Sync', options: ['', 'Draft', 'Pending', 'Synced', 'Failed'] },
]
const columns = [
  { key: 'retailer_name', label: 'Customer' },
  { key: 'retailer_type', label: 'Type' },
  { key: 'sales_rep_name', label: 'Sales Rep' },
  { key: 'phone', label: 'Phone' },
  { key: 'last_visit', label: 'Last Visit' },
  { key: 'sync_status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (m.tab === 'Active' || m.tab === 'Inactive') filters.push(['status', '=', m.tab])
  if (m.retailer_type) filters.push(['retailer_type', '=', m.retailer_type])
  if (m.sync_status) filters.push(['sync_status', '=', m.sync_status])
  return filters
}

const route = useRoute()
const { model, list, count, presetLabel, pageSize, refresh } = useDocList({
  doctype: 'Retailer',
  fields: ['name', 'retailer_name', 'city', 'retailer_type', 'sales_rep', 'sales_rep.full_name as sales_rep_name', 'phone', 'last_visit', 'status', 'sync_status'],
  orderBy: 'retailer_name asc',
  buildFilters,
  searchFields: ['retailer_name', 'name', 'city', 'phone', 'owner_name'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : route.query.tab || 'Active' },
  preset: route.query.preset || null,
})

function lastVisitClass(value) {
  const days = daysAgo(value)
  return days === null || days > 30 ? 'text-ink-red-5' : days > 7 ? 'text-ink-amber-7' : 'text-ink-gray-8'
}

// ---------------------------------------------------------------- import
// Store customers -> Retailers (managers only, enforced on the server too). As on
// Products, the run is done when its own summary Sync Log appears; the per-request
// logs of the same operation have no reference_doctype.
const session = useSessionStore()
const importing = ref(false)
const POLL_MS = 4000
const POLL_LIMIT_MS = 25 * 60 * 1000 // the job's own timeout
let pollTimer = null

async function lastImportLog() {
  const [log] = await call('frappe.client.get_list', {
    doctype: 'Sync Log',
    fields: ['name', 'status', 'response_payload', 'error_message', 'creation'],
    filters: { operation: 'Customer Sync', reference_doctype: 'Retailer' },
    order_by: 'creation desc',
    limit_page_length: 1,
  })
  return log || null
}

async function importCustomers() {
  importing.value = true
  try {
    const before = await lastImportLog()
    const result = await callMethod('field_sales_woocommerce.api.enqueue_customer_import')
    toast[result.queued ? 'info' : 'warning'](result.message)
    waitForImport(before?.creation || null, Date.now())
  } catch {
    // callMethod already toasted the error (e.g. Store Connection disabled).
    importing.value = false
  }
}

function waitForImport(after, pollStart) {
  pollTimer = setTimeout(async () => {
    try {
      const log = await lastImportLog()
      if (log && (!after || log.creation > after)) return finishImport(log)
    } catch {
      // A failed poll is retried on the next tick.
    }
    if (Date.now() - pollStart < POLL_LIMIT_MS) waitForImport(after, pollStart)
    else importing.value = false
  }, POLL_MS)
}

function finishImport(log) {
  importing.value = false
  if (log.status === 'Success') {
    const s = JSON.parse(log.response_payload || '{}')
    const text = `Customer import done: ${s.created ?? 0} created, ${s.linked ?? 0} linked, ${s.updated ?? 0} updated, ${s.unchanged ?? 0} unchanged, ${s.skipped ?? 0} skipped`
    if (s.failed) toast.warning(`${text}, ${s.failed} failed (see Sync Log)`)
    else toast.success(text)
  } else {
    toast.error(`Customer import failed: ${log.error_message || 'see Sync Log'}`)
  }
  refresh()
}

onBeforeUnmount(() => clearTimeout(pollTimer))
</script>
