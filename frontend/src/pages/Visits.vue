<template>
  <!-- Full height: the table fills what is left and scrolls inside its card -->
  <div class="flex h-full flex-col">
    <PageHeader title="Visits">
      <template #actions>
        <Button v-if="pageTab === 'visits'" label="New Visit" icon-left="lucide-plus" variant="solid" @click="newVisitOpen = true" />
      </template>
    </PageHeader>
    <div v-if="session.isManager" class="shrink-0 px-5 pb-3"><TabButtons v-model="pageTab" :options="pageTabs" /></div>
    <VisitImport v-if="pageTab === 'import'" />
    <div v-show="pageTab === 'visits'" class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5">
      <ListFilters v-model="model" search-placeholder="Search customer" :tabs="tabs" :fields="fields">
        <PresetChip :label="presetLabel" />
      </ListFilters>
      <DataTable
        :columns="columns"
        :rows="list.data || []"
        :loading="list.list.loading"
        :total="count.data"
        :has-more="list.hasNextPage"
        v-model:page-length="pageSize"
        :row-route="(row) => ({ name: 'Visit', params: { name: row.name } })"
        empty-icon="lucide-map-pin-check"
        empty-title="No visits found"
        @load-more="list.next()"
      >
        <template #cell-retailer_name="{ row }">
          <div class="font-medium text-ink-gray-9">{{ row.retailer_name }}</div>
          <div class="text-xs text-ink-gray-5">{{ [row.name, row.city].filter(Boolean).join(' · ') }}</div>
        </template>
        <template #cell-status="{ value }"><StatusPill :status="value" /></template>
        <template #cell-within_geofence="{ row }">
          <span v-if="!row.check_in_time" class="text-ink-gray-4">—</span>
          <span v-else :class="row.within_geofence ? 'text-ink-green-7' : 'text-ink-amber-7'">
            {{ row.within_geofence ? 'Inside' : `${Math.round(row.distance_from_retailer_m || 0)} m away` }}
          </span>
        </template>
      </DataTable>
    </div>
    <VisitDialog v-model="newVisitOpen" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, TabButtons } from 'frappe-ui'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import ListFilters from '@/components/ListFilters.vue'
import PresetChip from '@/components/PresetChip.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import VisitDialog from '@/components/VisitDialog.vue'
import VisitImport from '@/components/VisitImport.vue'
import { useDocList } from '@/composables/useDocList'
import { useSessionStore } from '@/stores/session'
import { formatDate, formatTime, today } from '@/utils/format'

const session = useSessionStore()
const newVisitOpen = ref(false)

// Page tab, kept in the URL (?tab=import) so a refresh or a link opens it. The list below
// stays mounted (hidden) while importing, so its filters are not lost. Importing is for
// Sales Managers / System Managers only (the server checks too).
const pageTabs = [
  { label: 'Visits', value: 'visits' },
  { label: 'Import from Excel', value: 'import' },
]
const route = useRoute()
const router = useRouter()
const pageTab = computed({
  get: () => (session.isManager && route.query.tab === 'import' ? 'import' : 'visits'),
  set: (value) => router.replace({ query: value === 'import' ? { tab: 'import' } : {} }),
})

const tabs = [
  { label: 'Today', value: 'today' },
  { label: 'Upcoming', value: 'upcoming' },
  { label: 'Past', value: 'past' },
  { label: 'All', value: 'all' },
]
const fields = [{ fieldname: 'status', label: 'Status', options: ['', 'Planned', 'In Progress', 'Completed', 'Cancelled'] }]
const columns = [
  { key: 'retailer_name', label: 'Customer' },
  { key: 'sales_rep_name', label: 'Sales Rep' },
  { key: 'visit_date', label: 'Date', format: formatDate },
  { key: 'check_in_time', label: 'Check-in', format: (v) => (v ? formatTime(v) : '—') },
  { key: 'time_on_site_min', label: 'On Site', align: 'right', format: (v, row) => (row.status === 'Completed' ? `${v || 0} min` : '—') },
  { key: 'within_geofence', label: 'Geofence' },
  { key: 'status', label: 'Status' },
]

function buildFilters(m) {
  const filters = []
  if (m.tab === 'today') filters.push(['visit_date', '=', today()])
  if (m.tab === 'upcoming') filters.push(['visit_date', '>', today()])
  if (m.tab === 'past') filters.push(['visit_date', '<', today()])
  if (m.status) filters.push(['status', '=', m.status])
  return filters
}

const { model, list, count, presetLabel, pageSize } = useDocList({
  doctype: 'Visit',
  fields: ['name', 'retailer_name', 'city', 'sales_rep_name', 'visit_date', 'status', 'check_in_time', 'time_on_site_min', 'within_geofence', 'distance_from_retailer_m'],
  orderBy: 'visit_date desc, modified desc',
  buildFilters,
  searchFields: ['retailer_name', 'name', 'city', 'sales_rep_name'],
  // ?preset=… (dashboard cards) shows exactly what the card counted, under the "All" tab;
  // ?tab=… opens a tab directly.
  initial: { tab: route.query.preset ? 'all' : (route.query.tab !== 'import' && route.query.tab) || 'today' },
  preset: route.query.preset || null,
})
</script>
