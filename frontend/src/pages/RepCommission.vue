<template>
  <div>
    <PageHeader title="Rep Commission">
      <template #meta>
        <LoadingIndicator v-if="report.loading && report.data" class="size-4 text-ink-gray-5" />
      </template>
      <template #actions>
        <Button label="Export" icon-left="lucide-download" :disabled="!rows.length" @click="exportCsv" />
        <Button icon="lucide-refresh-cw" variant="ghost" aria-label="Refresh" :loading="report.loading" @click="report.reload()" />
      </template>
    </PageHeader>

    <div class="space-y-5 px-5 pb-6">
      <!-- Filters: the same ones as the desk report -->
      <div class="fs-card flex flex-wrap items-end gap-3 p-4">
        <div class="w-36">
          <FormControl v-model="filters.from_date" type="date" label="From Date" />
        </div>
        <div class="w-36">
          <FormControl v-model="filters.to_date" type="date" label="To Date" />
        </div>
        <div class="w-56">
          <FormControl v-model="filters.sales_rep" type="select" label="Sales Rep" placeholder="All reps" :options="repOptions" />
        </div>
        <div class="w-56">
          <FormControl v-model="filters.manager" type="select" label="Manager" placeholder="All" :options="repOptions" />
        </div>
        <div v-if="sellLocations.multiple.value" class="w-48">
          <FormControl v-model="filters.location_id" type="select" label="Store Location" placeholder="All" :options="sellLocations.options.value" />
        </div>
        <Button v-if="isNarrowed" variant="ghost" label="Clear" icon-left="lucide-x" @click="clearFilters" />
      </div>

      <ErrorMessage v-if="report.error" :message="report.error" />

      <!-- Summary -->
      <div v-if="summary.length || !report.data" class="grid grid-cols-2 gap-4 md:grid-cols-4">
        <template v-if="summary.length">
          <NumberCard v-for="card in summary" :key="card.label" :label="card.label" :value="card.value" :theme="card.theme" />
        </template>
        <NumberCard v-for="i in 8" v-else :key="i" label="" loading />
      </div>

      <!-- Table -->
      <div class="fs-card overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-left">
            <thead>
              <tr class="border-b border-desk-border bg-surface-gray-1 text-ink-gray-5">
                <th
                  v-for="(col, i) in COLUMNS"
                  :key="col.fieldname"
                  scope="col"
                  class="whitespace-nowrap px-3 py-2 text-xs font-medium"
                  :class="[i > 0 && 'text-right', i === 0 && 'sticky left-0 z-20 min-w-56 border-r border-desk-border bg-surface-gray-1']"
                >
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <template v-if="report.loading && !rows.length">
                <tr v-for="i in 5" :key="'s' + i" class="border-b border-desk-border last:border-0">
                  <td v-for="col in COLUMNS" :key="col.fieldname" class="px-3 py-3">
                    <div class="h-3 w-3/4 animate-pulse rounded-3 bg-surface-gray-2" />
                  </td>
                </tr>
              </template>
              <tr
                v-for="row in rows"
                :key="row.sales_rep + row.currency"
                class="group border-b border-desk-border last:border-0 hover:bg-surface-gray-1"
              >
                <th
                  scope="row"
                  class="sticky left-0 z-10 whitespace-nowrap border-r border-desk-border bg-surface-base px-3 py-2 font-normal group-hover:bg-surface-gray-1"
                >
                  <RouterLink :to="{ name: 'SalesRep', params: { name: row.sales_rep } }" class="block text-ink-gray-9 hover:underline">
                    {{ row.rep_name }}
                  </RouterLink>
                  <span v-if="row.email" class="block text-xs text-ink-gray-5">{{ row.email }}</span>
                </th>
                <td
                  v-for="col in COLUMNS.slice(1)"
                  :key="col.fieldname"
                  class="whitespace-nowrap px-3 py-2 text-right tabular-nums"
                  :class="cellClass(col.fieldname, row)"
                >
                  {{ formatCell(col, row) }}
                </td>
              </tr>
            </tbody>
            <tfoot v-if="rows.length > 1">
              <tr class="border-t border-desk-border bg-surface-gray-1 font-semibold text-ink-gray-9">
                <th scope="row" class="sticky left-0 z-10 border-r border-desk-border bg-surface-gray-1 px-3 py-2 text-left">Total</th>
                <td v-for="col in COLUMNS.slice(1)" :key="col.fieldname" class="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                  {{ formatCell(col, total) }}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>

        <EmptyState
          v-if="report.data && !rows.length"
          icon="lucide-wallet"
          title="No commission in this period"
          description="No commission entries match these filters."
        />
        <div v-if="rows.length" class="border-t border-desk-border px-4 py-2 text-xs text-ink-gray-5">
          {{ rows.length }} {{ rows.length === 1 ? 'rep' : 'reps' }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, ErrorMessage, FormControl, LoadingIndicator, createResource, dayjs, debounce } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useSellLocations } from '@/composables/useSellLocations'
import { formatCurrency } from '@/utils/format'

// Runs the desk's "Rep Commission Summary" script report (frappe.desk.query_report.run checks the
// report's roles and the report scopes the reps), so the numbers here and in the desk always match.
const REPORT = 'Rep Commission Summary'
const COLUMNS = [
  { fieldname: 'sales_rep', label: 'Rep' },
  { fieldname: 'sales', label: 'Sales', fieldtype: 'Currency' },
  { fieldname: 'avg_discount', label: 'Avg Discount %', fieldtype: 'Percent' },
  { fieldname: 'earned', label: 'Earned', fieldtype: 'Currency' },
  { fieldname: 'pending', label: 'Pending', fieldtype: 'Currency' },
  { fieldname: 'reversed', label: 'Reversed', fieldtype: 'Currency' },
  { fieldname: 'override', label: 'Override', fieldtype: 'Currency' },
  { fieldname: 'net', label: 'Net', fieldtype: 'Currency' },
  // Paid: Net on statements marked Paid. Unpaid = Net - Paid (negative after a refund on paid commission).
  { fieldname: 'paid', label: 'Paid', fieldtype: 'Currency' },
  { fieldname: 'unpaid', label: 'Unpaid', fieldtype: 'Currency' },
]
const SUMMED = ['sales', 'earned', 'pending', 'reversed', 'override', 'net', 'paid', 'unpaid']

const route = useRoute()
const router = useRouter()

function defaults() {
  return {
    from_date: dayjs().startOf('month').format('YYYY-MM-DD'),
    to_date: dayjs().endOf('month').format('YYYY-MM-DD'),
    sales_rep: null,
    manager: null,
    location_id: null,
  }
}

// Filters live in the URL, so a refresh or a shared link opens the same view.
function fromQuery(query) {
  const f = defaults()
  for (const key of Object.keys(f)) {
    if (query[key] != null && query[key] !== '') f[key] = query[key]
  }
  return f
}

const filters = reactive(fromQuery(route.query))

const report = createResource({
  url: 'frappe.desk.query_report.run',
  makeParams: () => ({ report_name: REPORT, filters: { ...filters } }),
  auto: true,
})

const reload = debounce(() => report.reload(), 300)
watch(
  filters,
  () => {
    const query = {}
    const base = defaults()
    for (const [key, value] of Object.entries(filters)) {
      if (value && value !== base[key]) query[key] = value
    }
    router.replace({ query })
    if (filters.from_date && filters.to_date) reload()
  },
  { deep: true },
)

const isNarrowed = computed(() => JSON.stringify(filters) !== JSON.stringify(defaults()))
function clearFilters() {
  Object.assign(filters, defaults())
}

// Filter options and rep names / emails
const reps = createResource({
  url: 'frappe.client.get_list',
  params: { doctype: 'Sales Rep', fields: ['name', 'full_name', 'user'], order_by: 'full_name asc', limit_page_length: 0 },
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))
const sellLocations = useSellLocations()

// Result: the report appends its own total row as a plain list; the page builds the total from the rows.
const rows = computed(() => {
  const byName = Object.fromEntries((reps.data || []).map((r) => [r.name, r]))
  return (report.data?.result || [])
    .filter((row) => !Array.isArray(row))
    .map((row) => ({ ...row, rep_name: byName[row.sales_rep]?.full_name || row.sales_rep, email: byName[row.sales_rep]?.user }))
})

const total = computed(() => {
  const sum = Object.fromEntries(SUMMED.map((k) => [k, rows.value.reduce((s, r) => s + (r[k] || 0), 0)]))
  // Weighted by the discounted value, so it matches the discount over all the sales.
  const gross = rows.value.reduce((s, r) => s + (r.avg_discount < 100 ? r.sales / (1 - r.avg_discount / 100) : 0), 0)
  return { ...sum, avg_discount: gross ? ((gross - sum.sales) / gross) * 100 : 0 }
})

// Cells
const money = (value, digits = 2) => formatCurrency(value, rows.value[0]?.currency || undefined, digits)

function formatCell(col, row) {
  const value = row[col.fieldname]
  if (value == null || value === '') return '—'
  return col.fieldtype === 'Percent' ? `${Number(value).toFixed(1)}%` : money(value)
}

function cellClass(field, row) {
  const value = row[field]
  if (field === 'reversed' && value > 0) return 'text-ink-red-5'
  if (field === 'unpaid' && value < 0) return 'text-ink-red-5'
  if (field === 'net') return 'font-semibold text-ink-gray-9'
  return 'text-ink-gray-8'
}

// Summary cards, from the total
const summary = computed(() => {
  if (!report.data) return []
  const t = total.value
  return [
    { label: 'Sales', value: money(t.sales, 0) },
    { label: 'Earned', value: money(t.earned, 0) },
    { label: 'Pending', value: money(t.pending, 0) },
    { label: 'Reversed', value: money(t.reversed, 0), theme: t.reversed > 0 ? 'red' : null },
    { label: 'Override', value: money(t.override, 0) },
    { label: 'Net', value: money(t.net, 0), theme: 'green' },
    { label: 'Paid', value: money(t.paid, 0) },
    { label: 'Unpaid', value: money(t.unpaid, 0), theme: t.unpaid < 0 ? 'red' : null },
  ]
})

// CSV of every row, with raw numbers.
function exportCsv() {
  const cols = COLUMNS
  const escape = (v) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [['Rep', 'Email', ...cols.slice(1).map((c) => c.label)].map(escape).join(',')]
  for (const row of rows.value) {
    lines.push([row.rep_name, row.email, ...cols.slice(1).map((c) => row[c.fieldname])].map(escape).join(','))
  }
  lines.push(['Total', '', ...cols.slice(1).map((c) => total.value[c.fieldname])].map(escape).join(','))
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `Rep Commission ${filters.from_date} to ${filters.to_date}.csv`
  link.click()
  URL.revokeObjectURL(link.href)
}
</script>
