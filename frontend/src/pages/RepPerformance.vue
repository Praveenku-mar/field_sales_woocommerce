<template>
  <div>
    <PageHeader title="Rep Performance">
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
        <div class="w-44">
          <FormControl v-model="filters.compare_with" type="select" label="Compare With" :options="COMPARE_OPTIONS" />
        </div>
        <div class="w-36">
          <FormControl v-model="filters.group_by" type="select" label="Group By" :options="GROUP_OPTIONS" />
        </div>
        <div class="w-56">
          <FormControl
            v-model="filters.sales_rep"
            type="multiselect"
            label="Sales Rep"
            placeholder="All reps"
            :options="repOptions"
            :loading="reps.loading"
          />
        </div>
        <div class="w-40">
          <FormControl v-model="filters.territory" type="select" label="Territory" placeholder="All" :options="territories.data || []" />
        </div>
        <div class="w-40">
          <FormControl v-model="filters.city" type="select" label="City" placeholder="All" :options="cities.data || []" />
        </div>
        <div class="w-40">
          <FormControl v-model="filters.retailer_type" type="select" label="Customer Type" placeholder="All" :options="RETAILER_TYPES" />
        </div>
        <div class="flex h-7 items-center">
          <FormControl v-model="filters.show_only_below_target" type="checkbox" label="Only below target" />
        </div>
        <Button v-if="isNarrowed" variant="ghost" label="Clear" icon-left="lucide-x" @click="clearFilters" />
      </div>

      <ErrorMessage v-if="report.error" :message="report.error" />

      <!-- Summary -->
      <div v-if="summary.length || !report.data" class="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
        <template v-if="summary.length">
          <NumberCard v-for="card in summary" :key="card.label" :label="card.label" :value="card.value" :caption="card.caption">
            <template v-if="card.change" #badge>
              <Badge :theme="card.theme" :label="card.change" />
            </template>
          </NumberCard>
        </template>
        <NumberCard v-for="i in 8" v-else :key="i" label="" loading />
      </div>

      <!-- Chart -->
      <SectionCard v-if="chart" :title="chart.title">
        <div class="h-72">
          <LineChart
            v-if="chart.type === 'line'"
            :data="chart.data"
            x="label"
            :y="chart.y"
            :series-config="chart.series"
            :y-axis="{ format: (v) => money(v, 0) }"
            smooth
          />
          <BarChart
            v-else
            :data="chart.data"
            x="label"
            :y="chart.y"
            :series-config="chart.series"
            :y-axis="{ format: (v) => money(v, 0) }"
          />
        </div>
      </SectionCard>

      <!-- Table -->
      <div class="fs-card overflow-hidden">
        <div class="overflow-x-auto">
          <table class="w-full text-left">
            <thead>
              <tr class="border-b border-desk-border bg-surface-gray-1 text-ink-gray-5">
                <th
                  v-for="(col, i) in columns"
                  :key="col.fieldname"
                  scope="col"
                  class="whitespace-nowrap px-3 py-2 text-xs font-medium"
                  :class="[isNumeric(col) && 'text-right', i === 0 && 'sticky left-0 z-20 min-w-56 border-r border-desk-border bg-surface-gray-1']"
                >
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody>
              <template v-if="report.loading && !rows.length">
                <tr v-for="i in 5" :key="'s' + i" class="border-b border-desk-border last:border-0">
                  <td v-for="col in columns.slice(0, 8)" :key="col.fieldname" class="px-3 py-3">
                    <div class="h-3 w-3/4 animate-pulse rounded-3 bg-surface-gray-2" />
                  </td>
                </tr>
              </template>
              <tr
                v-for="row in visibleRows"
                :key="row.group_key"
                class="group border-b border-desk-border last:border-0 hover:bg-surface-gray-1"
              >
                <template v-for="(col, i) in columns" :key="col.fieldname">
                  <!-- First column: tree toggle, then the rep / group -->
                  <th
                    v-if="i === 0"
                    scope="row"
                    class="sticky left-0 z-10 whitespace-nowrap border-r border-desk-border bg-surface-base px-3 py-2 font-normal group-hover:bg-surface-gray-1"
                  >
                    <div class="flex items-center gap-1" :style="{ paddingLeft: (row.indent || 0) * 1.25 + 'rem' }">
                      <button
                        v-if="row.is_team"
                        type="button"
                        class="flex size-5 items-center justify-center rounded-3 text-ink-gray-5 hover:bg-surface-gray-3 focus-visible:bg-surface-gray-3 focus-visible:outline-none"
                        :aria-expanded="!collapsed.has(row.group_key)"
                        :aria-label="(collapsed.has(row.group_key) ? 'Expand ' : 'Collapse ') + row.group"
                        @click="toggle(row.group_key)"
                      >
                        <span :class="collapsed.has(row.group_key) ? 'lucide-chevron-right' : 'lucide-chevron-down'" class="size-4" />
                      </button>
                      <span v-else-if="isTree" class="size-5 shrink-0" />
                      <RouterLink
                        v-if="row.sales_rep"
                        :to="{ name: 'SalesRep', params: { name: row.sales_rep } }"
                        class="text-ink-gray-9 hover:underline"
                      >
                        {{ row.group }}
                      </RouterLink>
                      <span v-else :class="row.is_team ? 'font-semibold text-ink-gray-9' : 'text-ink-gray-8'">{{ row.group }}</span>
                    </div>
                  </th>
                  <td
                    v-else
                    class="whitespace-nowrap px-3 py-2"
                    :class="[isNumeric(col) && 'text-right tabular-nums', cellClass(col.fieldname, row)]"
                  >
                    {{ formatCell(col, row) }}
                  </td>
                </template>
              </tr>
            </tbody>
          </table>
        </div>

        <EmptyState
          v-if="report.data && !rows.length"
          icon="lucide-chart-column"
          title="No data"
          description="No sales reps match these filters."
        />
        <div v-if="rows.length" class="flex items-center justify-between border-t border-desk-border px-4 py-2 text-xs text-ink-gray-5">
          <span>{{ rows.length }} {{ rows.length === 1 ? 'row' : 'rows' }}</span>
          <span v-if="isTree" class="flex gap-3">
            <button type="button" class="hover:text-ink-gray-8" @click="collapsed = new Set()">Expand all</button>
            <button type="button" class="hover:text-ink-gray-8" @click="collapseAll">Collapse all</button>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Badge, Button, ErrorMessage, FormControl, LoadingIndicator, createResource, dayjs, debounce } from 'frappe-ui'
import { BarChart, LineChart } from 'frappe-ui/charts'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import { formatCurrency, formatNumber } from '@/utils/format'

// Runs the desk's "Rep Performance" script report (frappe.desk.query_report.run checks the
// report's roles), so the numbers here and in the desk always match.
const REPORT = 'Rep Performance'
const COMPARE_OPTIONS = ['None', 'Previous Period', 'Same Period Last Year']
const GROUP_OPTIONS = ['Sales Rep', 'Territory', 'City', 'Retailer Type', 'Day', 'Week', 'Month']
const RETAILER_TYPES = ['Kirana', 'Supermarket', 'Wholesaler', 'Medical', 'Other']
const NUMERIC = ['Int', 'Float', 'Currency', 'Percent']

const route = useRoute()
const router = useRouter()

function defaults() {
  return {
    from_date: dayjs().startOf('month').format('YYYY-MM-DD'),
    to_date: dayjs().endOf('month').format('YYYY-MM-DD'),
    compare_with: 'Previous Period',
    group_by: 'Sales Rep',
    sales_rep: [],
    territory: null,
    city: null,
    retailer_type: null,
    show_only_below_target: false,
  }
}

// Filters live in the URL, so a refresh or a shared link opens the same view.
function fromQuery(query) {
  const f = defaults()
  for (const key of Object.keys(f)) {
    const value = query[key]
    if (value == null || value === '') continue
    if (key === 'sales_rep') f.sales_rep = [].concat(value)
    else if (key === 'show_only_below_target') f.show_only_below_target = value === '1'
    else f[key] = value
  }
  return f
}

const filters = reactive(fromQuery(route.query))

const report = createResource({
  url: 'frappe.desk.query_report.run',
  makeParams: () => ({
    report_name: REPORT,
    filters: {
      ...filters,
      sales_rep: filters.sales_rep.length ? filters.sales_rep : null,
      show_only_below_target: filters.show_only_below_target ? 1 : 0,
    },
    is_tree: filters.group_by === 'Sales Rep',
    parent_field: 'parent_key',
  }),
  auto: true,
  onSuccess: () => collapseAll(),
})

const reload = debounce(() => report.reload(), 300)
watch(
  filters,
  () => {
    const query = {}
    const base = defaults()
    for (const [key, value] of Object.entries(filters)) {
      if (key === 'sales_rep' ? value.length : key === 'show_only_below_target' ? value : value && value !== base[key]) {
        query[key] = key === 'show_only_below_target' ? '1' : value
      }
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

// Filter options
const reps = createResource({
  url: 'frappe.client.get_list',
  params: { doctype: 'Sales Rep', fields: ['name', 'full_name'], order_by: 'full_name asc', limit_page_length: 0 },
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))
const territories = createResource({ url: 'field_sales_woocommerce.api.get_territories', auto: true })
const cities = createResource({ url: 'field_sales_woocommerce.api.get_retailer_cities', auto: true })

// Result
const columns = computed(() => report.data?.columns || [])
const rows = computed(() => report.data?.result || [])
const isTree = computed(() => rows.value.some((r) => r.is_team))

// Tree: teams below the top level start collapsed, as in the desk (initial_depth 1).
const collapsed = ref(new Set())
function collapseAll() {
  collapsed.value = new Set(rows.value.filter((r) => r.is_team && r.indent >= 1).map((r) => r.group_key))
}
function toggle(key) {
  const next = new Set(collapsed.value)
  next.has(key) ? next.delete(key) : next.add(key)
  collapsed.value = next
}
const visibleRows = computed(() => {
  const parentOf = Object.fromEntries(rows.value.map((r) => [r.group_key, r.parent_key]))
  return rows.value.filter((row) => {
    for (let p = row.parent_key; p; p = parentOf[p]) {
      if (collapsed.value.has(p)) return false
    }
    return true
  })
})

// Cells
const money = (value, digits = 2) => formatCurrency(value, undefined, digits)
const isNumeric = (col) => NUMERIC.includes(col.fieldtype)

function formatCell(col, row) {
  const value = row[col.fieldname]
  if (value == null || value === '') return '—'
  switch (col.fieldtype) {
    case 'Currency':
      return money(value)
    case 'Percent':
      return col.fieldname === 'value_delta_pct' ? `${value > 0 ? '▲' : value < 0 ? '▼' : ''} ${Math.abs(value).toFixed(1)}%` : `${value.toFixed(1)}%`
    case 'Int':
    case 'Float':
      return formatNumber(value, col.precision ?? 0)
    default:
      return value
  }
}

// Same highlights as the desk report's formatter; the delta also carries an arrow.
function cellClass(field, row) {
  const value = row[field]
  if (value == null) return 'text-ink-gray-8'
  if (field === 'achievement_pct') return value < 70 ? 'font-medium text-ink-red-5' : value < 100 ? 'font-medium text-ink-amber-7' : 'font-medium text-ink-green-7'
  if (field === 'completion_pct' && value < 80) return 'font-medium text-ink-red-5'
  if (field === 'value_delta_pct') return value > 0 ? 'text-ink-green-7' : value < 0 ? 'text-ink-red-5' : 'text-ink-gray-5'
  if (field === 'failed_syncs' && value > 0) return 'font-medium text-ink-red-5'
  return row.is_team ? 'font-medium text-ink-gray-9' : 'text-ink-gray-8'
}

// Summary cards: the server appends " · ▲ 4.2%" to a label when comparing.
const summary = computed(() =>
  (report.data?.report_summary || []).map((card) => {
    const [label, change] = card.label.split(' · ')
    const value =
      card.datatype === 'Currency' ? money(card.value, 0) : card.datatype === 'Percent' ? `${flt(card.value).toFixed(1)}%` : formatNumber(card.value)
    return {
      label,
      value: card.value == null ? null : value,
      change,
      theme: { Green: 'green', Red: 'red' }[card.indicator] || 'gray',
      caption: change ? `vs ${filters.compare_with.toLowerCase()}` : null,
    }
  }),
)
const flt = (v) => Number(v) || 0

// Chart: the report's chart data, drawn with frappe-ui charts.
const chart = computed(() => {
  const c = report.data?.chart
  const datasets = c?.data?.datasets
  if (!datasets?.length) return null
  const keys = datasets.map((_, i) => `s${i}`)
  const data = c.data.labels.map((label, i) => Object.fromEntries([['label', label], ...keys.map((k, j) => [k, datasets[j].values[i]])]))
  const series = Object.fromEntries(
    datasets.map((d, i) => [keys[i], { label: d.name, type: d.chartType === 'line' ? 'line' : undefined, format: (v) => money(v, 0) }]),
  )
  return {
    type: c.type === 'line' ? 'line' : 'bar',
    title: c.type === 'line' ? 'Order value over time' : 'Order value vs target',
    data,
    y: keys,
    series,
  }
})

// CSV of every row (collapsed ones too), with raw numbers.
function exportCsv() {
  const cols = columns.value
  const escape = (v) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [cols.map((c) => escape(c.label)).join(',')]
  for (const row of rows.value) {
    lines.push(cols.map((c, i) => escape(i === 0 ? '  '.repeat(row.indent || 0) + row.group : row[c.fieldname])).join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `Rep Performance ${filters.from_date} to ${filters.to_date}.csv`
  link.click()
  URL.revokeObjectURL(link.href)
}
</script>
