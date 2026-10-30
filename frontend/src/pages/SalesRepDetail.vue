<template>
  <div>
    <PageHeader :title="d.full_name || name" :status="d.name ? (d.active ? 'Active' : 'Inactive') : null">
      <template #meta><span class="text-ink-gray-5">{{ name }}</span></template>
      <template #actions>
        <Dropdown v-if="d.name && moreActions.length" :options="moreActions" align="end">
          <Button variant="ghost" icon="lucide-ellipsis" aria-label="More actions" />
        </Dropdown>
        <Button v-if="canSendReset" label="Send Reset Link" icon-left="lucide-mail" @click="openReset" />
        <Button
          v-if="canChangePassword"
          :label="isSelf ? 'Change Password' : 'Set Password'"
          icon-left="lucide-key-round"
          @click="openPassword"
        />
        <Button v-if="canEdit" label="Edit" icon-left="lucide-pencil" variant="solid" @click="openEdit" />
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Sales rep not found" :description="errorText" /></div></div>

    <div v-else class="grid grid-cols-1 items-start gap-4 px-5 pb-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="space-y-4">
        <SectionCard title="Details">
          <InfoGrid :items="details" :columns="3" />
        </SectionCard>

        <SectionCard v-if="team.data?.length" :title="`Team (${team.data.length})`" flush>
          <DataTable :columns="teamColumns" :rows="team.data" :row-route="(r) => ({ name: 'SalesRep', params: { name: r.name } })">
            <template #cell-full_name="{ row }">
              <div class="font-medium text-ink-gray-9">{{ row.full_name }}</div>
              <div class="text-xs text-ink-gray-5">{{ row.name }}</div>
            </template>
            <template #cell-achievement="{ row }"><Achievement :pct="teamStats.data?.[row.name]?.achievement_pct ?? null" /></template>
            <template #cell-active="{ row }"><StatusPill :status="row.active ? 'Active' : 'Inactive'" /></template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Recent Visits" flush>
          <DataTable :columns="visitColumns" :rows="visits.data || []" :loading="visits.list.loading" :row-route="(r) => ({ name: 'Visit', params: { name: r.name } })" empty-icon="lucide-map-pin" empty-title="No visits yet">
            <template #cell-status="{ value }"><StatusPill :status="value" /></template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Recent Orders" flush>
          <DataTable :columns="orderColumns" :rows="orders.data || []" :loading="orders.list.loading" :row-route="(r) => ({ name: 'Order', params: { name: r.name } })" empty-icon="lucide-shopping-cart" empty-title="No orders yet">
            <template #cell-sync_status="{ row }"><StatusPill :status="orderStatus(row)" /></template>
          </DataTable>
        </SectionCard>
      </div>

      <div class="space-y-4">
        <div class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-chart-column size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">This Month</h2>
            <span class="text-ink-gray-5">{{ monthLabel }}</span>
          </div>
          <div v-if="month.loading && !month.data" class="flex justify-center py-8"><LoadingIndicator class="size-4" /></div>
          <div v-else-if="!m" class="px-4 py-6 text-center text-ink-gray-5">No activity this month</div>
          <template v-else>
            <div class="space-y-2 px-4 py-3">
              <div class="flex items-baseline justify-between">
                <span class="text-2xl font-semibold tabular-nums text-ink-gray-9">{{ formatCurrency(m.order_value, undefined, 0) }}</span>
                <span v-if="m.period_target" class="text-ink-gray-5">of {{ formatCurrency(m.period_target, undefined, 0) }}</span>
              </div>
              <Achievement :pct="m.achievement_pct" />
              <p v-if="m.period_target" class="text-xs text-ink-gray-5">
                Target to date, pro-rated from {{ formatCurrency(m.monthly_target, undefined, 0) }} a month
              </p>
            </div>
            <dl class="divide-y divide-outline-gray-1 border-t border-desk-border px-4">
              <div v-for="item in monthRows" :key="item.label" class="flex items-center justify-between py-2.5">
                <dt class="text-ink-gray-6">{{ item.label }}</dt>
                <dd class="tabular-nums" :class="item.alert ? 'font-medium text-ink-red-6' : 'text-ink-gray-8'">{{ item.value }}</dd>
              </div>
            </dl>
          </template>
          <div class="p-4 pt-1">
          </div>
        </div>

        <SectionCard title="Customers">
          <div class="flex items-baseline justify-between">
            <span class="text-2xl font-semibold tabular-nums text-ink-gray-9">{{ retailerCount.data ?? '—' }}</span>
            <span class="text-ink-gray-5">active customers assigned</span>
          </div>
        </SectionCard>
      </div>
    </div>

    <Dialog v-model="editOpen" title="Edit sales rep" size="lg">
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormControl v-model="edit.mobile" label="Mobile" type="tel" class="sm:col-span-2" />
        <FormControl v-model="edit.territory" label="Territory" />
        <FormControl v-model.number="edit.monthly_target" label="Monthly Target" type="number" min="0" />
        <FormControl
          v-model.number="edit.max_discount_pct"
          label="Max Discount % per Order"
          type="number"
          min="0"
          max="100"
          description="Empty = no discount"
        />
        <div class="space-y-1.5 sm:col-span-2">
          <div class="text-sm text-ink-gray-6">Reports To</div>
          <LinkField
            v-model="edit.reports_to"
            class="w-full"
            doctype="Sales Rep"
            title-field="full_name"
            description-field="territory"
            :search-fields="['full_name', 'name', 'territory']"
            :filters="{ active: 1, name: ['!=', name] }"
            :label="manager.data?.full_name || d.reports_to"
            placeholder="No manager"
          />
        </div>
        <div v-if="session.commissionEnabled" class="space-y-1.5 sm:col-span-2">
          <div class="text-sm text-ink-gray-6">Commission Plan</div>
          <LinkField
            v-model="edit.commission_plan"
            class="w-full"
            doctype="Commission Plan"
            :filters="{ is_active: 1 }"
            :label="d.commission_plan"
            placeholder="No plan"
          />
          <Checkbox v-model="edit.commission_active" label="Commission Active" />
        </div>
        <Checkbox v-model="edit.active" label="Active" class="sm:col-span-2" />
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="editOpen = false" />
          <Button variant="solid" label="Save" :loading="doc.setValue.loading" @click="saveEdit" />
        </div>
      </template>
    </Dialog>

    <Dialog v-model="passwordOpen" :title="isSelf ? 'Change password' : 'Set password'" size="md" @after-leave="resetPassword">
      <form id="change-password" class="space-y-4" @submit.prevent="savePassword">
        <p class="text-sm text-ink-gray-6">
          <template v-if="isSelf">Change the password you sign in with ({{ d.user }}).</template>
          <template v-else>
            New password for <span class="font-medium text-ink-gray-9">{{ d.full_name || d.user }}</span> ({{ d.user }}).
          </template>
        </p>
        <FormControl
          v-if="isSelf"
          v-model="password.old"
          label="Current Password"
          :type="password.show ? 'text' : 'password'"
          autocomplete="current-password"
          required
        />
        <FormControl
          v-model="password.value"
          label="New Password"
          :type="password.show ? 'text' : 'password'"
          autocomplete="new-password"
          required
        >
          <template #suffix>
            <button
              type="button"
              class="flex text-ink-gray-5 hover:text-ink-gray-8"
              :aria-label="password.show ? 'Hide password' : 'Show password'"
              @click="password.show = !password.show"
            >
              <span :class="password.show ? 'lucide-eye-off' : 'lucide-eye'" class="size-4" />
            </button>
          </template>
        </FormControl>
        <FormControl
          v-model="password.confirm"
          label="Confirm Password"
          :type="password.show ? 'text' : 'password'"
          autocomplete="new-password"
          required
        />
        <p v-if="passwordMismatch" class="text-xs text-ink-red-5" aria-live="polite">The passwords do not match.</p>
        <Checkbox v-model="password.logout" :label="isSelf ? 'Sign out of my other devices' : 'Sign the rep out of all devices'" />
      </form>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" :disabled="password.saving" @click="passwordOpen = false" />
          <Button
            type="submit"
            form="change-password"
            variant="solid"
            label="Update Password"
            :loading="password.saving"
            :disabled="!passwordReady"
          />
        </div>
      </template>
    </Dialog>

    <!-- Reset link (someone else's password) -->
    <Dialog v-model="resetOpen" title="Send password reset link" size="md">
      <div class="space-y-4">
        <p class="text-base text-ink-gray-7">
          Email a password reset link to <span class="font-medium text-ink-gray-9">{{ d.full_name || d.user }}</span> ({{ d.user }})?
          The link works once and expires after the time set in System Settings.
        </p>
        <Checkbox v-model="reset.logout" label="Also sign the rep out of all devices now" />
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" :disabled="reset.sending" @click="resetOpen = false" />
          <Button variant="solid" label="Send Link" icon-left="lucide-mail" :loading="reset.sending" @click="sendReset" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  Button,
  Checkbox,
  Dialog,
  Dropdown,
  FormControl,
  LoadingIndicator,
  createDocumentResource,
  createListResource,
  createResource,
  toast,
} from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import Achievement from '@/components/Achievement.vue'
import LinkField from '@/components/LinkField.vue'
import { useSessionStore } from '@/stores/session'
import { formatCurrency, formatDate, formatNumber, orderStatus } from '@/utils/format'
import { callMethod } from '@/utils/api'

const props = defineProps({ name: { type: String, required: true } })
const session = useSessionStore()

const doc = createDocumentResource({ doctype: 'Sales Rep', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))
// Sales Rep is writable by managers only (doctype permissions).
const canEdit = computed(() => !!d.value.name && session.isManager)
// Your own page: Change Password (with the current password). Someone else's: managers
// email a reset link; System Managers can also set the password directly (when email is
// down). The server keeps users with roles beyond Field Sales Rep to System Managers.
const isSelf = computed(() => !!d.value.user && d.value.user === session.user)
const canChangePassword = computed(() => !!d.value.user && (isSelf.value || (canEdit.value && session.isAdmin)))
const canSendReset = computed(() => !!d.value.user && !isSelf.value && canEdit.value)

// The manager's name; the document only stores their ID.
const manager = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({ doctype: 'Sales Rep', filters: d.value.reports_to, fieldname: 'full_name' }),
})
watch(
  () => d.value.reports_to,
  (value) => value && manager.fetch(),
  { immediate: true },
)

const details = computed(() => [
  { label: 'Email', value: d.value.user, href: d.value.user && `mailto:${d.value.user}` },
  { label: 'Mobile', value: d.value.mobile, href: d.value.mobile && `tel:${d.value.mobile}` },
  { label: 'Territory', value: d.value.territory },
  {
    label: 'Reports To',
    value: d.value.reports_to ? manager.data?.full_name || d.value.reports_to : null,
    route: d.value.reports_to && { name: 'SalesRep', params: { name: d.value.reports_to } },
  },
  { label: 'Monthly Target', value: d.value.monthly_target ? formatCurrency(d.value.monthly_target, undefined, 0) : null },
  { label: 'Max Discount', value: d.value.max_discount_pct > 0 ? `${d.value.max_discount_pct}%` : 'No discount' },
  ...(session.commissionEnabled
    ? [
        {
          label: 'Commission Plan',
          value: d.value.commission_plan ? `${d.value.commission_plan}${d.value.commission_active ? '' : ' (commission off)'}` : 'No plan',
        },
      ]
    : []),
])

// ---------------------------------------------------------------- related lists

const team = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name', 'territory', 'active'],
  filters: { reports_to: props.name },
  orderBy: 'full_name asc',
  pageLength: 50,
  auto: true,
})
const teamStats = createResource({ url: 'field_sales_woocommerce.frontend_api.get_rep_month_stats' })
watch(
  () => team.data?.length,
  (n) => n && teamStats.fetch(),
)
const teamColumns = [
  { key: 'full_name', label: 'Name' },
  { key: 'territory', label: 'Territory' },
  { key: 'achievement', label: 'This Month', width: '180px' },
  { key: 'active', label: 'Status' },
]

const visits = createListResource({
  doctype: 'Visit',
  fields: ['name', 'retailer_name', 'city', 'visit_date', 'status', 'time_on_site_min'],
  filters: { sales_rep: props.name },
  orderBy: 'visit_date desc, modified desc',
  pageLength: 5,
  auto: true,
})
const visitColumns = [
  { key: 'visit_date', label: 'Date', format: formatDate },
  { key: 'retailer_name', label: 'Customer' },
  { key: 'city', label: 'City' },
  { key: 'time_on_site_min', label: 'On Site', align: 'right', format: (v, r) => (r.status === 'Completed' ? `${v || 0} min` : '—') },
  { key: 'status', label: 'Status' },
]

const orders = createListResource({
  doctype: 'Field Order',
  fields: ['name', 'retailer_name', 'order_date', 'grand_total', 'docstatus', 'sync_status'],
  filters: { sales_rep: props.name, docstatus: ['!=', 2] },
  orderBy: 'order_date desc, modified desc',
  pageLength: 5,
  auto: true,
})
const orderColumns = [
  { key: 'name', label: 'Order' },
  { key: 'retailer_name', label: 'Customer' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'grand_total', label: 'Total', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'sync_status', label: 'Status' },
]

const retailerCount = createResource({
  url: 'frappe.desk.reportview.get_count',
  params: { doctype: 'Retailer', filters: { sales_rep: props.name, status: 'Active' } },
  auto: true,
})

// ---------------------------------------------------------------- this month

const month = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_rep_month_stats',
  params: { sales_rep: props.name },
  auto: true,
})
const m = computed(() => month.data?.[props.name] || null)
const monthLabel = new Date().toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
const pct = (v) => (v == null ? '—' : `${formatNumber(v, 1)}%`)
const monthRows = computed(() => [
  { label: 'Orders', value: `${formatNumber(m.value.orders)}${m.value.avg_order_value ? ` · avg ${formatCurrency(m.value.avg_order_value, undefined, 0)}` : ''}` },
  { label: 'Visits completed', value: `${formatNumber(m.value.completed_visits)} of ${formatNumber(m.value.planned_visits)}` },
  { label: 'Missed visits', value: formatNumber(m.value.missed_visits), alert: m.value.missed_visits > 0 },
  { label: 'Visit → order', value: pct(m.value.conversion_pct) },
  { label: 'Customers visited', value: formatNumber(m.value.retailers_visited) },
  { label: 'Failed syncs', value: formatNumber(m.value.failed_syncs), alert: m.value.failed_syncs > 0 },
])


const moreActions = computed(() => [
].filter((a) => !a.condition || a.condition()))

// ---------------------------------------------------------------- edit

const editOpen = ref(false)
const edit = reactive({})
// full_name is fetched from the User, so it is not editable here.
const EDIT_FIELDS = ['mobile', 'territory', 'monthly_target', 'max_discount_pct', 'reports_to', 'active', 'commission_plan', 'commission_active']

function openEdit() {
  EDIT_FIELDS.forEach((f) => (edit[f] = d.value[f] ?? null))
  edit.active = !!d.value.active
  edit.commission_active = !!d.value.commission_active
  editOpen.value = true
}

async function saveEdit() {
  try {
    // The plan is saved through its own method, which checks it is an active plan.
    const { commission_plan, commission_active, ...rest } = edit
    await doc.setValue.submit({ ...rest, active: edit.active ? 1 : 0, monthly_target: edit.monthly_target || 0,
      max_discount_pct: edit.max_discount_pct === '' || edit.max_discount_pct == null ? null : edit.max_discount_pct,
    })
    if ((commission_plan || null) !== (d.value.commission_plan || null) || !!commission_active !== !!d.value.commission_active) {
      await callMethod('field_sales_woocommerce.commission.api.assign_commission_plan', {
        sales_rep: props.name,
        commission_plan: commission_plan || null,
        commission_active: commission_active ? 1 : 0,
      })
      await doc.reload()
    }
    editOpen.value = false
    toast.success('Sales rep updated')
    month.reload()
  } catch (e) {
    toast.error(e?.messages?.[0] || e?.message || 'Could not save')
  }
}

// ---------------------------------------------------------------- password

const passwordOpen = ref(false)
const emptyPassword = () => ({ old: '', value: '', confirm: '', show: false, logout: true, saving: false })
const password = reactive(emptyPassword())
const passwordMismatch = computed(() => !!password.confirm && password.value !== password.confirm)
const passwordReady = computed(
  () => !!password.value && password.value === password.confirm && (!isSelf.value || !!password.old),
)

function openPassword() {
  resetPassword()
  passwordOpen.value = true
}

function resetPassword() {
  Object.assign(password, emptyPassword())
}

async function savePassword() {
  if (!passwordReady.value) return
  password.saving = true
  try {
    await callMethod('field_sales_woocommerce.api.set_sales_rep_password', {
      sales_rep: props.name,
      new_password: password.value,
      logout_all_sessions: password.logout ? 1 : 0,
      old_password: isSelf.value ? password.old : undefined,
    })
    passwordOpen.value = false
    toast.success('Password updated')
  } catch {
    // callMethod already toasted the error (e.g. the password policy). The input is kept.
  } finally {
    password.saving = false
  }
}

// ---------------------------------------------------------------- reset link

const resetOpen = ref(false)
const reset = reactive({ logout: false, sending: false })

function openReset() {
  Object.assign(reset, { logout: false, sending: false })
  resetOpen.value = true
}

async function sendReset() {
  reset.sending = true
  try {
    const { email } = await callMethod('field_sales_woocommerce.api.send_sales_rep_password_reset', {
      sales_rep: d.value.name,
      logout_all_sessions: reset.logout ? 1 : 0,
    })
    resetOpen.value = false
    toast.success(`Reset link sent to ${email}`)
  } catch {
    // callMethod already toasted the error (e.g. no outgoing email).
  } finally {
    reset.sending = false
  }
}
</script>
