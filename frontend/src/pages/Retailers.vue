<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Customers">
      <template #actions>
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
import { ref } from 'vue'
import { Button } from 'frappe-ui'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import RetailerDialog from '@/components/RetailerDialog.vue'
import { useDocList } from '@/composables/useDocList'
import { daysAgo, relativeDay } from '@/utils/format'

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
const { model, list, count, presetLabel, pageSize } = useDocList({
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
</script>
