<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Sync Log" />
    <div class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search record, error, endpoint" :tabs="tabs" :fields="fields">
        <div v-if="reference" class="flex items-center gap-1.5 rounded-full bg-surface-gray-3 py-1 pl-3 pr-1.5 text-sm text-ink-gray-8">
          <span class="lucide-filter size-3.5 text-ink-gray-5" />
          <span>Showing: <b class="font-medium">{{ reference }}</b></span>
          <button class="flex size-5 items-center justify-center rounded-full hover:bg-surface-gray-4" title="Show all" @click="clearReference">
            <span class="lucide-x size-3.5" />
          </button>
        </div>
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        v-model:page-length="pageSize"
        :row-route="(row) => ({ name: 'SyncLogEntry', params: { name: row.name } })"
        empty-icon="lucide-scroll-text"
        empty-title="No sync log entries"
        @load-more="list.next()"
      >
        <template #cell-creation="{ value }">
          <div class="whitespace-nowrap text-ink-gray-9">{{ relativeDay(value) }}, {{ formatTime(value) }}</div>
          <div class="text-xs text-ink-gray-5">{{ formatDate(value) }}</div>
        </template>
        <template #cell-operation="{ row }">
          <div class="text-ink-gray-9">{{ row.operation }}</div>
          <div class="text-xs text-ink-gray-5">{{ row.platform }}</div>
        </template>
        <template #cell-reference_name="{ row }">
          <template v-if="row.reference_name">
            <div class="text-ink-gray-9">{{ row.reference_name }}</div>
            <div class="text-xs text-ink-gray-5">{{ row.reference_doctype }}</div>
          </template>
          <span v-else class="text-ink-gray-4">—</span>
        </template>
        <template #cell-error_message="{ row }">
          <div v-if="row.error_message || row.error_code" class="line-clamp-2 max-w-md text-ink-red-6">
            <span v-if="row.error_code" class="font-mono text-xs">{{ row.error_code }} </span>{{ row.error_message }}
          </div>
          <span v-else class="text-ink-gray-4">—</span>
        </template>
        <template #cell-status="{ value }"><StatusPill :status="value" /></template>
      </DataTable>
    </div>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useDocList } from '@/composables/useDocList'
import { formatDate, formatNumber, formatTime, relativeDay } from '@/utils/format'

const route = useRoute()
const router = useRouter()

// ?reference=FO-2026-00011 (from an order's "View Sync Log") shows that record's entries only.
const reference = route.query.reference || null
function clearReference() {
  const query = { ...route.query }
  delete query.reference
  router.replace({ query })
}

const tabs = [
  { label: 'All', value: 'all' },
  { label: 'Failed', value: 'Failed' },
  { label: 'Retrying', value: 'Retrying' },
  { label: 'Success', value: 'Success' },
]
const fields = [
  {
    fieldname: 'operation',
    label: 'Operation',
    options: ['', 'Test Connection', 'Token Refresh', 'Product Sync', 'Stock Check', 'Create Customer', 'Update Customer', 'Create Order', 'Confirm Order', 'Other'],
  },
  { fieldname: 'reference_doctype', label: 'Record type', options: ['', 'Field Order', 'Retailer', 'Platform Product', 'Store Connection'] },
]
const columns = [
  { key: 'creation', label: 'When', width: '150px' },
  { key: 'operation', label: 'Operation' },
  { key: 'reference_name', label: 'Record' },
  { key: 'error_message', label: 'Error' },
  { key: 'attempt', label: 'Attempt', align: 'right', format: (v) => (v ? `#${v}` : '—') },
  { key: 'duration_ms', label: 'Duration', align: 'right', format: (v) => (v == null ? '—' : `${formatNumber(v)} ms`) },
  { key: 'status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (reference) filters.push(['reference_name', '=', reference])
  if (m.tab && m.tab !== 'all') filters.push(['status', '=', m.tab])
  if (m.operation) filters.push(['operation', '=', m.operation])
  if (m.reference_doctype) filters.push(['reference_doctype', '=', m.reference_doctype])
  return filters
}

const { model, list, count, pageSize } = useDocList({
  doctype: 'Sync Log',
  fields: ['name', 'creation', 'platform', 'operation', 'reference_doctype', 'reference_name', 'status', 'attempt', 'duration_ms', 'error_code', 'error_message'],
  orderBy: 'creation desc',
  buildFilters,
  searchFields: ['reference_name', 'error_message', 'error_code', 'endpoint', 'external_id'],
  initial: { tab: route.query.tab || 'all' },
})
</script>
