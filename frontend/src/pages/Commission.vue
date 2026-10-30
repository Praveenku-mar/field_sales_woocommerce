<template>
  <div class="flex h-full flex-col">
    <PageHeader title="Commission">
      <template #actions>
        <TabButtons v-if="tab === 'mine' || tab === 'team'" v-model="period" :options="PERIODS" />
        <Button v-if="session.isManager && tab === 'plans'" label="New Plan" icon-left="lucide-plus" @click="editPlan(null)" />
        <Button v-if="session.isManager && tab === 'statements'" label="Generate Statements" icon-left="lucide-file-plus" @click="generateOpen = true" />
        <Button v-if="session.isManager" label="New Adjustment" icon-left="lucide-plus-minus" @click="adjustOpen = true" />
        <Button icon="lucide-refresh-cw" variant="ghost" aria-label="Refresh" :loading="loading" @click="reload" />
      </template>
    </PageHeader>

    <div
      class="flex min-h-0 flex-1 flex-col gap-3 px-5 pb-5"
      :class="tab === 'mine' && 'overflow-y-auto xl:overflow-hidden'"
    >
      <div class="shrink-0"><TabButtons v-model="tab" :options="tabs" /></div>

      <!-- My Commission -->
      <template v-if="tab === 'mine'">
        <div class="grid shrink-0 grid-cols-2 gap-4 md:grid-cols-4">
          <NumberCard label="Earned" :value="money(summary.data?.earned)" :loading="!summary.data" />
          <NumberCard label="Pending" :value="money(summary.data?.pending)" :loading="!summary.data" />
          <NumberCard label="Reversed" :value="money(summary.data?.reversed)" :loading="!summary.data" theme="red" />
          <NumberCard label="Net" :value="money(summary.data?.net)" :loading="!summary.data" theme="green" />
        </div>

        <div v-if="gap" class="fs-card flex shrink-0 items-center gap-2 px-4 py-3 text-ink-gray-8">
          <span class="lucide-trending-up size-4 text-ink-gray-6" />
          <span>
            <span class="font-medium">{{ formatCurrency(gap.amount, currency, 0) }}</span> more sales this month reaches the
            <span class="font-medium">{{ gap.rate }}%</span> slab
          </span>
        </div>

        <div class="grid shrink-0 grid-cols-1 gap-4 xl:min-h-0 xl:flex-1 xl:shrink xl:grid-cols-[minmax(0,1fr)_320px] xl:grid-rows-[minmax(0,1fr)]">
            <DataTable
              class="min-h-[24rem] xl:min-h-0"
              :columns="entryColumns"
              :rows="entries"
              :loading="entriesLoading"
              :total="entriesTotal"
              v-model:page-length="entriesPageSize"
              :has-more="entries.length < entriesTotal"
              empty-icon="lucide-wallet"
              empty-title="No commission entries"
              empty-description="Entries appear here when your orders sync."
              @load-more="loadEntries(false)"
            >
              <template #cell-order="{ row }">
                <Tooltip v-if="row.entry_type === 'Adjustment'" :text="row.adjustment_reason" :disabled="!row.adjustment_reason">
                  <span class="cursor-help text-ink-gray-8 underline decoration-dotted">
                    Adjustment{{ row.beneficiary_type === 'Manager Override' ? ' (override)' : '' }}
                  </span>
                </Tooltip>
                <RouterLink
                  v-else-if="row.source_order"
                  :to="{ name: 'Order', params: { name: row.source_order } }"
                  class="hover:underline"
                >
                  {{ row.source_order }}
                </RouterLink>
                <span v-else>—</span>
              </template>
              <template #cell-product="{ row }">
                <span v-if="row.entry_type === 'Adjustment'" class="text-ink-gray-6">{{ row.adjustment_reason || '—' }}</span>
                <span v-else>{{ row.product || '—' }}</span>
              </template>
              <template #cell-effective_rate="{ row }">
                <span v-if="row.entry_type === 'Adjustment'">—</span>
                <Tooltip v-else :text="row.calculation_note" :disabled="!row.calculation_note">
                  <span :class="row.calculation_note && 'cursor-help underline decoration-dotted'">{{ formatRate(row.effective_rate) }}</span>
                </Tooltip>
              </template>
              <template #cell-status="{ value }"><StatusPill :status="value" /></template>
            </DataTable>

          <SectionCard title="Top Products" class="xl:min-h-0 xl:overflow-y-auto">
            <ul v-if="summary.data?.top_products?.length" class="divide-y divide-outline-gray-1">
              <li v-for="p in summary.data.top_products" :key="p.product" class="flex items-center justify-between gap-3 py-2">
                <span class="truncate text-ink-gray-8">{{ p.title || p.product }}</span>
                <span class="tabular-nums text-ink-gray-9">{{ money(p.commission) }}</span>
              </li>
            </ul>
            <div v-else class="py-4 text-center text-ink-gray-5">No products yet</div>
          </SectionCard>
        </div>
      </template>

      <!-- Team -->
      <DataTable
          v-else-if="tab === 'team'"
          class="flex-1"
          :columns="teamColumns"
          :rows="teamShown"
          :loading="team.loading"
          v-model:page-length="teamPageSize"
          :total="teamRows.length"
          :has-more="teamShownCount < teamRows.length"
          @load-more="teamShownCount += teamPageSize"
          :row-route="(r) => ({ name: 'SalesRep', params: { name: r.name } })"
          empty-icon="lucide-users"
          empty-title="No team members"
        >
          <template #cell-full_name="{ row }">
            <div class="font-medium text-ink-gray-9">{{ row.full_name || row.sales_rep }}</div>
            <div class="text-xs text-ink-gray-5">{{ row.sales_rep }}</div>
          </template>
          <template #cell-commission_plan="{ row }">
            <template v-if="row.commission_plan">
              <span>{{ row.commission_plan }}</span>
              <span v-if="!row.commission_active" class="ml-1.5 text-xs text-ink-gray-5">(off)</span>
            </template>
            <span v-else class="text-ink-gray-5">No plan</span>
          </template>
          <template #cell-actions="{ row }">
            <Button size="sm" label="Assign plan" @click.stop="assignTo = row; assignOpen = true" />
          </template>
      </DataTable>

      <!-- Plans (managers) -->
      <DataTable
          v-else-if="tab === 'plans'"
          class="flex-1"
          :columns="planColumns"
          :rows="plansShown"
          :loading="plans.loading"
          v-model:page-length="plansPageSize"
          :total="plansAll.length"
          :has-more="plansShownCount < plansAll.length"
          @load-more="plansShownCount += plansPageSize"
          empty-icon="lucide-percent"
          empty-title="No commission plans"
          empty-description="Create a plan, then assign it to your reps from the Team tab."
          row-clickable
          @row-click="editPlan"
        >
          <template #cell-plan_name="{ row }">
            <span class="font-medium text-ink-gray-9">{{ row.plan_name }}</span>
          </template>
          <template #cell-summary="{ row }">{{ planSummary(row) }}</template>
          <template #cell-is_active="{ row }"><StatusPill :status="row.is_active ? 'Active' : 'Inactive'" /></template>
      </DataTable>

      <!-- Statements -->
      <DataTable
          v-else
          class="flex-1"
          :columns="statementColumns"
          :rows="statements"
          :loading="statementsLoading"
          :total="statementsTotal"
          v-model:page-length="statementsPageSize"
          :has-more="statements.length < statementsTotal"
          :row-route="(r) => ({ name: 'CommissionStatement', params: { name: r.name } })"
          empty-icon="lucide-file-text"
          empty-title="No statements yet"
          empty-description="Statements are drafted on the 1st of each month. Managers can also generate them now."
          @load-more="loadStatements(false)"
        >
          <template #cell-full_name="{ row }">
            <div class="font-medium text-ink-gray-9">{{ row.full_name || row.sales_rep }}</div>
            <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
          </template>
          <template #cell-status="{ value }"><StatusPill :status="value" /></template>
      </DataTable>
    </div>

    <template v-if="session.isManager">
      <CommissionPlanDialog v-model="planOpen" :plan="editingPlan" @saved="plans.reload(); team.reload()" />
      <AssignPlanDialog v-model="assignOpen" :rep="assignTo" :plans="plans.data || []" @saved="team.reload()" />
      <CommissionAdjustmentDialog v-model="adjustOpen" @saved="onAdjusted" />
      <GenerateStatementsDialog v-model="generateOpen" @generated="loadStatements()" />
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button, TabButtons, Tooltip, createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import CommissionPlanDialog from '@/components/CommissionPlanDialog.vue'
import AssignPlanDialog from '@/components/AssignPlanDialog.vue'
import CommissionAdjustmentDialog from '@/components/CommissionAdjustmentDialog.vue'
import GenerateStatementsDialog from '@/components/GenerateStatementsDialog.vue'
import { useSessionStore } from '@/stores/session'
import { callMethod } from '@/utils/api'
import { formatCurrency, formatDate, formatNumber } from '@/utils/format'

const PERIODS = [
  { label: 'This month', value: 'this_month' },
  { label: 'Last month', value: 'last_month' },
  { label: 'This year', value: 'this_year' },
  { label: 'All', value: 'all' },
]

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

const hasRep = computed(() => !!session.salesRep)
const period = ref('this_month')

// Team: managers always; a rep only when they have people reporting to them.
const team = createResource({
  url: 'field_sales_woocommerce.commission.api.get_team_commission',
  makeParams: () => ({ period: period.value }),
  auto: true,
})
const teamRows = computed(() => (team.data || []).map((r) => ({ ...r, name: r.sales_rep })))
const teamPageSize = ref(20)
const teamShownCount = ref(20)
watch(teamPageSize, (n) => (teamShownCount.value = n))
const teamShown = computed(() => teamRows.value.slice(0, teamShownCount.value))
const hasTeam = computed(() => session.isManager || (team.data || []).some((r) => r.sales_rep !== session.salesRep?.name))

const tabs = computed(() => [
  ...(hasRep.value ? [{ label: 'My Commission', value: 'mine' }] : []),
  ...(hasTeam.value ? [{ label: 'Team', value: 'team' }] : []),
  { label: 'Statements', value: 'statements' },
  ...(session.isManager ? [{ label: 'Plans', value: 'plans' }] : []),
])

// The tab is in the URL (?tab=team), so a refresh or a link opens it.
const tab = computed({
  get: () => {
    const wanted = route.query.tab
    if (tabs.value.some((t) => t.value === wanted)) return wanted
    // Team may still be loading for a rep: keep the requested tab rather than falling back.
    if (wanted === 'team' && team.loading) return 'team'
    return hasRep.value ? 'mine' : session.isManager ? 'team' : 'statements'
  },
  set: (value) => router.replace({ query: { tab: value } }),
})

const loading = computed(() => summary.loading || team.loading || plans.loading || entriesLoading.value || statementsLoading.value)

// Plans (managers)
const plans = createResource({ url: 'field_sales_woocommerce.commission.api.get_commission_plans' })
watch(
  () => session.isManager,
  (isManager) => isManager && plans.fetch(),
  { immediate: true },
)
const plansAll = computed(() => plans.data || [])
const plansPageSize = ref(20)
const plansShownCount = ref(20)
watch(plansPageSize, (n) => (plansShownCount.value = n))
const plansShown = computed(() => plansAll.value.slice(0, plansShownCount.value))
const planOpen = ref(false)
const editingPlan = ref(null)
function editPlan(plan) {
  editingPlan.value = plan
  planOpen.value = true
}
const planColumns = [
  { key: 'plan_name', label: 'Plan' },
  { key: 'rule_type', label: 'Rule Type' },
  { key: 'summary', label: 'Rate' },
  { key: 'reps', label: 'Reps', align: 'right' },
  { key: 'is_active', label: 'Status' },
]
function planSummary(p) {
  const pct = (v) => `${formatNumber(v, 2)}%`
  if (p.rule_type === 'Flat %') return pct(p.flat_rate)
  if (p.rule_type === 'Per Order') return `${formatCurrency(p.per_order_amount)} per order`
  if (p.rule_type === 'Product Rate') {
    const n = p.rates.length
    return `${n} ${n === 1 ? 'rate' : 'rates'}` + (p.default_rate ? `, default ${pct(p.default_rate)}` : '')
  }
  const rates = p.slabs.map((s) => s.rate)
  if (!rates.length) return '—'
  const lo = Math.min(...rates)
  const hi = Math.max(...rates)
  return `${p.slabs.length} slabs, ${lo === hi ? pct(lo) : `${pct(lo)} to ${pct(hi)}`} (${p.slab_mode === 'Marginal' ? 'marginal' : 'whole amount'})`
}

// Assign plan, adjustments, statement generation
const assignOpen = ref(false)
const assignTo = ref(null)
const adjustOpen = ref(false)
const generateOpen = ref(false)
function onAdjusted() {
  if (hasRep.value) {
    summary.reload()
    loadEntries()
  }
  team.reload()
}

// My Commission
const summary = createResource({
  url: 'field_sales_woocommerce.commission.api.get_my_commission_summary',
  makeParams: () => ({ period: period.value }),
  auto: hasRep.value,
})
const currency = computed(() => summary.data?.currency)
const gap = computed(() => summary.data?.next_slab_gap)
const money = (value) => (value == null ? null : formatCurrency(value, currency.value))
const formatRate = (v) => (v == null ? '—' : `${formatNumber(v, 2)}%`)

const entriesPageSize = ref(20)
watch(entriesPageSize, () => loadEntries())
const entries = ref([])
const entriesTotal = ref(0)
const entriesLoading = ref(false)
async function loadEntries(reset = true) {
  if (!hasRep.value) return
  entriesLoading.value = true
  try {
    const page = reset ? 1 : Math.floor(entries.value.length / entriesPageSize.value) + 1
    const res = await callMethod('field_sales_woocommerce.commission.api.get_commission_entries', {
      filters: { sales_rep: session.salesRep.name, ...periodFilter() },
      page,
      page_length: entriesPageSize.value,
    })
    entries.value = reset ? res.entries : [...entries.value, ...res.entries]
    entriesTotal.value = res.total
  } finally {
    entriesLoading.value = false
  }
}

// Entries filter on posting date; the summary uses earned dates, so totals can differ slightly.
function periodFilter() {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth()
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  const range = {
    this_month: [new Date(y, m, 1), new Date(y, m + 1, 0)],
    last_month: [new Date(y, m - 1, 1), new Date(y, m, 0)],
    this_year: [new Date(y, 0, 1), new Date(y, 11, 31)],
  }[period.value]
  return range ? { posting_date: ['between', [iso(range[0]), iso(range[1])]] } : {}
}

const entryColumns = [
  { key: 'posting_date', label: 'Date', format: formatDate },
  { key: 'order', label: 'Order' },
  { key: 'product', label: 'Product' },
  { key: 'base_amount', label: 'Base', align: 'right', format: (v, r) => formatCurrency(v, r.currency) },
  { key: 'effective_rate', label: 'Rate', align: 'right' },
  { key: 'amount', label: 'Amount', align: 'right', format: (v, r) => formatCurrency(v, r.currency) },
  { key: 'reversed_amount', label: 'Reversed', align: 'right', format: (v, r) => (v ? formatCurrency(v, r.currency) : '—') },
  { key: 'status', label: 'Status' },
]

// Team
const teamColumns = computed(() => [
  { key: 'full_name', label: 'Sales Rep' },
  { key: 'commission_plan', label: 'Plan' },
  { key: 'earned', label: 'Earned', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'pending', label: 'Pending', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'reversed', label: 'Reversed', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'override', label: 'Override', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'net', label: 'Net', align: 'right', class: 'font-medium', format: (v) => formatCurrency(v) },
  ...(session.isManager ? [{ key: 'actions', label: '', align: 'right' }] : []),
])

// Statements
const statementsPageSize = ref(20)
watch(statementsPageSize, () => loadStatements())
const statements = ref([])
const statementsTotal = ref(0)
const statementsLoading = ref(false)
async function loadStatements(reset = true) {
  statementsLoading.value = true
  try {
    const page = reset ? 1 : Math.floor(statements.value.length / statementsPageSize.value) + 1
    const res = await callMethod('field_sales_woocommerce.commission.api.get_commission_statements', { page, page_length: statementsPageSize.value })
    statements.value = reset ? res.statements : [...statements.value, ...res.statements]
    statementsTotal.value = res.total
  } finally {
    statementsLoading.value = false
  }
}
const statementColumns = [
  { key: 'full_name', label: 'Sales Rep' },
  { key: 'period_start', label: 'Period', format: (v, r) => `${formatDate(v)} – ${formatDate(r.period_end)}` },
  { key: 'net_payable', label: 'Net Payable', align: 'right', class: 'font-medium', format: (v, r) => formatCurrency(v, r.currency) },
  { key: 'status', label: 'Status' },
]

function reload() {
  if (hasRep.value) {
    summary.reload()
    loadEntries()
  }
  team.reload()
  loadStatements()
  if (session.isManager) plans.reload()
}

watch(period, () => {
  if (hasRep.value) {
    summary.reload()
    loadEntries()
  }
  team.reload()
})
onMounted(() => {
  loadEntries()
  loadStatements()
})
</script>
