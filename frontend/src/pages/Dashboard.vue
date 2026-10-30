<template>
  <div>
    <PageHeader :title="greeting">
      <template #meta>
        <TabButtons v-model="period" :options="PERIODS" />
        <LoadingIndicator v-if="dash.loading && dash.data" class="size-4 text-ink-gray-5" />
      </template>
      <template #actions>
        <Button icon="lucide-refresh-cw" variant="ghost" aria-label="Refresh" :loading="dash.loading || overview.loading" @click="refresh" />
      </template>
    </PageHeader>

    <!-- First load -->
    <div v-if="!dash.data && !dash.error" class="flex h-64 items-center justify-center text-ink-gray-5">
      <LoadingIndicator class="size-5" />
    </div>

    <!-- Nothing to show: the first load failed -->
    <div v-else-if="!dash.data" class="px-5 pb-6">
      <div class="fs-card flex flex-col items-center gap-3 p-8">
        <ErrorMessage :message="dash.error" />
        <Button label="Retry" icon-left="lucide-refresh-cw" @click="refresh" />
      </div>
    </div>

    <div v-else class="space-y-5 px-5 pb-6">
      <!-- A reload failed: keep the last numbers and say so -->
      <ErrorMessage v-if="dash.error || overview.error" :message="dash.error || overview.error" />

      <!-- The selected period -->
      <div class="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <NumberCard label="Visits" icon="lucide-map-pin-check" :value="cards.visits_count" :caption="periodLabel" />
        <NumberCard label="Orders" icon="lucide-shopping-cart" :value="cards.orders_count" :caption="periodLabel" />
        <NumberCard
          label="Order Value"
          icon="lucide-indian-rupee"
          :value="money(cards.order_value, 0)"
          :caption="periodLabel"
          class="col-span-2 lg:col-span-1"
        />
        <NumberCard label="Pending Sync" icon="lucide-clock" :value="cards.pending_sync_count" caption="Orders waiting to reach the store" />
        <NumberCard
          label="Failed Sync"
          icon="lucide-triangle-alert"
          :value="cards.failed_sync_count"
          caption="Orders and customers"
          :to="session.isManager ? { name: 'SyncFailures' } : null"
        >
          <template v-if="cards.failed_sync_count" #badge>
            <Badge theme="red" label="Needs attention" />
          </template>
        </NumberCard>
      </div>

      <!-- Today -->
      <div>
        <h2 class="mb-2 text-sm font-medium text-ink-gray-6">Today</h2>
        <div class="grid grid-cols-2 gap-4 xl:grid-cols-4">
          <NumberCard
            label="Visits Today"
            icon="lucide-map-pin-check"
            :value="t.visits_completed"
            :caption="o.today ? `${t.visits_completed} of ${t.visits_planned} completed` : null"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { tab: 'today' } }"
          />
          <NumberCard
            label="In Progress"
            icon="lucide-timer"
            :value="t.visits_in_progress"
            caption="Reps checked in right now"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { tab: 'today' } }"
          />
          <NumberCard
            label="Orders Today"
            icon="lucide-shopping-cart"
            :value="t.orders"
            caption="Submitted today"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'orders_today' } }"
          />
          <NumberCard
            label="Order Value Today"
            icon="lucide-indian-rupee"
            :value="o.today ? money(t.order_value, 0) : null"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'orders_today' } }"
          />
        </div>
      </div>

      <!-- Needs attention -->
      <div>
        <h2 class="mb-2 text-sm font-medium text-ink-gray-6">Needs attention</h2>
        <div class="grid grid-cols-2 gap-4 lg:grid-cols-3">
          <NumberCard
            label="Missed Visits"
            icon="lucide-calendar-x"
            :value="a.missed_visits"
            :theme="a.missed_visits ? 'red' : null"
            caption="Planned, not done (7 days)"
            :loading="!overview.data"
            :to="{ name: 'Visits', query: { preset: 'missed_visits' } }"
          />
          <NumberCard
            label="Not Visited in 30 Days"
            icon="lucide-store"
            :value="a.not_visited_30d"
            caption="Active customers"
            :loading="!overview.data"
            :to="{ name: 'Retailers', query: { preset: 'not_visited_30d' } }"
          />
          <NumberCard
            label="Draft Orders"
            icon="lucide-file-pen"
            :value="a.draft_orders"
            caption="Not yet submitted"
            :loading="!overview.data"
            :to="{ name: 'Orders', query: { preset: 'draft_orders' } }"
          />
          <NumberCard
            label="Pending Customers"
            icon="lucide-user-round-cog"
            :value="a.pending_retailers"
            caption="Waiting to sync to the store"
            :loading="!overview.data"
            :to="{ name: 'Retailers', query: { preset: 'pending_retailers' } }"
          />
          <NumberCard
            label="Out of Stock Products"
            icon="lucide-package-x"
            :value="a.out_of_stock"
            :theme="a.out_of_stock ? 'red' : null"
            caption="Active, tracked, none left"
            :loading="!overview.data"
            :to="{ name: 'Products', query: { preset: 'out_of_stock' } }"
          />
        </div>
      </div>

      <!-- Charts for the selected period -->
      <div class="grid grid-cols-1 gap-5" :class="session.isManager && 'lg:grid-cols-2'">
        <SectionCard title="Orders by day">
          <div class="h-64">
            <BarChart
              v-if="hasOrders"
              :data="ordersByDay"
              x="label"
              y="orders"
              :series-config="{ orders: { label: 'Orders', color: COLORS.orders } }"
              :tooltip-columns="[{ name: 'value', label: 'Order value', format: (v) => money(v) }]"
            />
            <EmptyState v-else icon="lucide-chart-column" title="No orders" :description="`No orders were submitted ${periodLabel.toLowerCase()}.`" />
          </div>
        </SectionCard>

        <SectionCard v-if="session.isManager" title="Orders by rep">
          <div class="h-64">
            <BarChart
              v-if="data.orders_by_rep.length"
              :data="data.orders_by_rep"
              x="rep_name"
              y="value"
              horizontal
              :series-config="{ value: { label: 'Order value', color: COLORS.value, format: (v) => money(v, 0) } }"
              :tooltip-columns="[{ name: 'orders', label: 'Orders' }]"
            />
            <EmptyState v-else icon="lucide-users" title="No orders" description="Reps with submitted orders appear here." />
          </div>
        </SectionCard>
      </div>

      <!-- Trends (same charts as the desk workspace) -->
      <div class="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <SectionCard title="Visits – Last 30 Days" class="xl:col-span-2">
          <div class="h-60">
            <LineChart
              :data="c.visits_30d || []"
              x="date"
              y="visits"
              :loading="!overview.data"
              :series-config="{ visits: { label: 'Completed visits', color: COLORS.visits } }"
              smooth
            />
          </div>
        </SectionCard>
        <SectionCard title="Visits by Status – This Month">
          <div class="h-60">
            <DonutChart
              :data="visitsByStatus"
              category="status"
              value="count"
              :palette="visitsByStatus.map((row) => STATUS_COLORS[row.status] || COLORS.neutral)"
              :loading="!overview.data"
            />
          </div>
        </SectionCard>
        <SectionCard title="Order Value – Last 30 Days" class="xl:col-span-3">
          <div class="h-60">
            <AreaChart
              :data="c.order_value_30d || []"
              x="date"
              y="value"
              :loading="!overview.data"
              :series-config="{ value: { label: 'Order value', color: COLORS.value, format: (v) => money(v, 0) } }"
            />
          </div>
        </SectionCard>
      </div>

      <div class="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <SectionCard title="Recent Orders" flush>
          <template #actions>
            <Button label="All orders" variant="ghost" :route="{ name: 'Orders' }" />
          </template>
          <ListView
            v-if="data.recent_orders.length"
            :columns="orderColumns"
            :rows="data.recent_orders"
            row-key="name"
            :options="{
              selectable: false,
              showTooltip: false,
              getRowRoute: (row) => ({ name: 'Order', params: { name: row.name } }),
            }"
          >
            <template #cell="{ column, row, item }">
              <Badge v-if="column.key === 'sync_status'" :theme="STATUS_THEMES[orderStatus(row)] || 'gray'" :label="orderStatus(row)" />
              <span v-else-if="column.key === 'grand_total'" class="tabular-nums">{{ money(item) }}</span>
              <span v-else-if="column.key === 'modified'" class="text-ink-gray-5" :title="formatDateTime(item)">{{ fromNow(item) }}</span>
              <span v-else class="truncate">{{ item }}</span>
            </template>
          </ListView>
          <EmptyState v-else icon="lucide-shopping-cart" title="No orders yet" description="Orders your team takes appear here." />
        </SectionCard>

        <SectionCard title="Active Visits" flush>
          <template #actions>
            <Button label="All visits" variant="ghost" :route="{ name: 'Visits', query: { tab: 'today' } }" />
          </template>
          <ListView
            v-if="data.active_visits.length"
            :columns="visitColumns"
            :rows="data.active_visits"
            row-key="name"
            :options="{
              selectable: false,
              showTooltip: false,
              getRowRoute: (row) => ({ name: 'Visit', params: { name: row.name } }),
            }"
          >
            <template #cell="{ column, row, item }">
              <div v-if="column.key === 'rep_name'" class="flex min-w-0 items-center gap-2">
                <Avatar :label="item || row.sales_rep" size="sm" />
                <span class="truncate">{{ item || row.sales_rep }}</span>
              </div>
              <span v-else-if="column.key === 'check_in_time'" class="text-ink-gray-5" :title="formatDateTime(item)">{{ fromNow(item) }}</span>
              <span v-else class="truncate">{{ item }}</span>
            </template>
          </ListView>
          <EmptyState v-else icon="lucide-map-pin-off" title="No one is checked in" description="Visits that are checked in but not out appear here." />
        </SectionCard>

        <SectionCard title="Today's Visits" flush>
          <template #actions>
            <Button label="All visits" variant="ghost" :route="{ name: 'Visits', query: { tab: 'today' } }" />
          </template>
          <ul v-if="o.visits_today?.length" class="divide-y divide-desk-border">
            <li v-for="v in o.visits_today" :key="v.name">
              <RouterLink :to="{ name: 'Visit', params: { name: v.name } }" class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span class="lucide-map-pin size-4 shrink-0 text-ink-gray-4" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ v.retailer_name }}</div>
                  <div class="truncate text-xs text-ink-gray-5">
                    {{ [v.sales_rep_name, v.city].filter(Boolean).join(' · ') }}
                    <template v-if="v.check_in_time"> · in at {{ formatTime(v.check_in_time) }}</template>
                  </div>
                </div>
                <StatusPill :status="v.status" />
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-calendar" title="No visits today" description="Visits planned for today appear here." />
        </SectionCard>

        <SectionCard title="Sync Failures" flush>
          <template #actions>
            <Button v-if="session.isManager" label="All failures" variant="ghost" :route="{ name: 'SyncFailures' }" />
          </template>
          <ul v-if="o.failures?.length" class="divide-y divide-desk-border">
            <li v-for="f in o.failures" :key="f.type + f.name">
              <RouterLink :to="failureRoute(f)" class="flex items-start gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span :class="[f.type === 'Order' ? 'lucide-shopping-cart' : 'lucide-store', 'mt-0.5 size-4 shrink-0 text-ink-red-5']" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ f.title || f.name }}</div>
                  <div class="truncate text-xs text-ink-red-5">{{ f.sync_error || 'Sync failed' }}</div>
                </div>
                <span class="text-xs text-ink-gray-5">{{ f.type === "Retailer" ? "Customer" : f.type }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-circle-check" title="No sync failures" description="Every order and customer reached the store." />
        </SectionCard>

        <SectionCard title="Draft Orders" flush class="xl:col-span-2">
          <template #actions>
            <Button label="All drafts" variant="ghost" :route="{ name: 'Orders', query: { tab: 'draft' } }" />
          </template>
          <ul v-if="o.draft_orders?.length" class="divide-y divide-desk-border">
            <li v-for="d in o.draft_orders" :key="d.name">
              <RouterLink :to="{ name: 'Order', params: { name: d.name } }" class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1">
                <span class="lucide-file-pen size-4 shrink-0 text-ink-gray-4" />
                <div class="min-w-0 flex-1">
                  <div class="truncate font-medium text-ink-gray-9">{{ d.retailer_name }}</div>
                  <div class="truncate text-xs text-ink-gray-5">{{ d.name }} · {{ formatDate(d.order_date) }}</div>
                </div>
                <span class="tabular-nums text-ink-gray-8">{{ money(d.grand_total) }}</span>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else-if="overview.data" icon="lucide-file-check" title="No draft orders" description="Every order has been submitted." />
        </SectionCard>
      </div>

      <!-- Team (managers) -->
      <SectionCard v-if="o.team?.length" title="Team this month" flush>
        <div class="overflow-x-auto">
          <table class="w-full min-w-[36rem] text-left">
            <thead>
              <tr class="border-b border-desk-border text-xs text-ink-gray-5">
                <th class="px-4 py-2 font-medium">Sales Rep</th>
                <th class="px-4 py-2 text-right font-medium">Visits</th>
                <th class="px-4 py-2 text-right font-medium">Orders</th>
                <th class="px-4 py-2 text-right font-medium">Order Value</th>
                <th class="w-56 px-4 py-2 font-medium">Target</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in o.team" :key="r.sales_rep" class="border-b border-desk-border last:border-0">
                <td class="px-4 py-2.5">
                  <div class="font-medium text-ink-gray-9">{{ r.sales_rep_name || r.sales_rep }}</div>
                  <div class="text-xs text-ink-gray-5">{{ r.territory || '—' }}</div>
                </td>
                <td class="px-4 py-2.5 text-right tabular-nums">{{ r.completed_visits }}</td>
                <td class="px-4 py-2.5 text-right tabular-nums">{{ r.orders }}</td>
                <td class="px-4 py-2.5 text-right tabular-nums">{{ money(r.order_value, 0) }}</td>
                <td class="px-4 py-2.5">
                  <Achievement :pct="r.achievement_pct" />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Avatar, Badge, Button, ErrorMessage, LoadingIndicator, TabButtons, createResource, dayjs } from 'frappe-ui'
import { ListView } from 'frappe-ui/experimental'
import { AreaChart, BarChart, DonutChart, LineChart } from 'frappe-ui/charts'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import Achievement from '@/components/Achievement.vue'
import { useSessionStore } from '@/stores/session'
import { formatCurrency, formatDate, formatDateTime, formatTime, orderStatus } from '@/utils/format'

const PERIODS = [
  { label: 'Today', value: 'today' },
  { label: 'This Week', value: 'week' },
  { label: 'This Month', value: 'month' },
]
const STATUS_THEMES = { Synced: 'green', Pending: 'amber', Failed: 'red', Draft: 'gray', Cancelled: 'gray' }

const orderColumns = [
  { label: 'Order', key: 'name', width: '9rem' },
  { label: 'Customer', key: 'retailer_name', width: 2 },
  { label: 'Amount', key: 'grand_total', width: '7rem', align: 'right' },
  { label: 'Sync', key: 'sync_status', width: '6rem' },
  { label: 'Modified', key: 'modified', width: '7rem' },
]
const visitColumns = [
  { label: 'Rep', key: 'rep_name', width: 2 },
  { label: 'Customer', key: 'retailer_name', width: 2 },
  { label: 'Checked in', key: 'check_in_time', width: '8rem' },
]

const session = useSessionStore()
const period = ref('today')

// The selected period: top cards, the two bar charts, recent orders and active visits.
const dash = createResource({
  url: 'field_sales_woocommerce.api.dashboard.get_dashboard',
  makeParams: () => ({ period: period.value }),
  auto: true,
})
watch(period, () => dash.reload())

// Not tied to the period: today's cards, needs attention, 30-day trends, today's visits,
// sync failures, draft orders and the team table.
const overview = createResource({ url: 'field_sales_woocommerce.frontend_api.get_dashboard', auto: true })

function refresh() {
  dash.reload()
  overview.reload()
}

const data = computed(() => dash.data || {})
const cards = computed(() => data.value.cards || {})
const o = computed(() => overview.data || {})
const t = computed(() => o.value.today || {})
const a = computed(() => o.value.attention || {})
const c = computed(() => o.value.charts || {})
const periodLabel = computed(() => PERIODS.find((p) => p.value === period.value).label)

// Short day labels ("13 Oct") on the axis.
const ordersByDay = computed(() =>
  (data.value.orders_by_day || []).map((row) => ({ ...row, label: dayjs(row.date).format('D MMM') })),
)
const hasOrders = computed(() => ordersByDay.value.some((row) => row.orders))

// Each measure keeps one hue on every chart: orders blue, order value violet, completed
// visits emerald. Read from frappe-ui's chart ramp (echarts needs concrete colors, not var()).
// Emerald, blue and violet stay apart for colour-blind readers in any pairing; amber and red
// do not sit next to emerald, so they are left out.
const css = getComputedStyle(document.documentElement)
const token = (name) => css.getPropertyValue(name).trim()
const COLORS = {
  orders: token('--chart-categorical-1'),
  visits: token('--chart-categorical-3'),
  value: token('--chart-categorical-5'),
  neutral: token('--ink-gray-4'),
}
// A visit status keeps its color whatever its share: Completed matches the visits line.
const STATUS_COLORS = { Completed: COLORS.visits, 'In Progress': COLORS.orders, Planned: COLORS.value, Cancelled: COLORS.neutral }

// DonutChart sorts slices largest first and colors them in that order, so sort the same way
// here (stable for ties) and pass the palette in the same order.
const visitsByStatus = computed(() => [...(c.value.visits_by_status || [])].sort((x, y) => y.count - x.count))

const greeting = computed(() => {
  const hour = new Date().getHours()
  const part = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'
  return `${part}, ${(session.fullName || '').split(' ')[0]}`
})

function failureRoute(f) {
  return f.type === 'Order' ? { name: 'Order', params: { name: f.name } } : { name: 'Retailer', params: { name: f.name } }
}

// Money in the site currency, like every other page (utils/format.js).
function money(value, digits = 2) {
  return value == null ? '—' : formatCurrency(value, undefined, digits)
}

function fromNow(value) {
  return value ? dayjs(value).fromNow() : '—'
}
</script>
