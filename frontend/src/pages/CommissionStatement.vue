<template>
  <div>
    <PageHeader :title="d.full_name || d.sales_rep || name" :status="d.name ? d.status : null">
      <template #meta>
        <span class="text-ink-gray-5">{{ periodLabel }}</span>
      </template>
      <template #actions>
        <template v-if="d.can_manage && d.docstatus === 0">
          <Button label="Regenerate" icon-left="lucide-refresh-cw" @click="askRegenerate" />
          <Button label="Approve" icon-left="lucide-check" variant="solid" @click="askApprove" />
        </template>
        <Button v-if="d.can_manage && d.status === 'Approved'" label="Mark Paid" icon-left="lucide-banknote" variant="solid" @click="openPay" />
      </template>
    </PageHeader>

    <div v-if="statement.error" class="px-5">
      <div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Statement not found" :description="errorText" /></div>
    </div>

    <div v-else class="space-y-4 px-5 pb-6">
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <NumberCard label="Total Sales" :value="money(d.total_sales)" :loading="!d.name" />
        <NumberCard label="Slab Rate" :value="d.name ? `${formatNumber(d.slab_rate_applied, 2)}%` : null" :loading="!d.name" />
        <NumberCard label="Gross Commission" :value="money(d.gross_commission)" :loading="!d.name" />
        <NumberCard label="Reversals" :value="money(d.reversals)" :loading="!d.name" theme="red" />
        <NumberCard label="Override" :value="money(d.override_commission)" :loading="!d.name" />
        <NumberCard label="Net Payable" :value="money(d.net_payable)" :loading="!d.name" theme="green" />
      </div>

      <SectionCard title="Details">
        <InfoGrid :items="details" :columns="3" />
      </SectionCard>

      <SectionCard :title="`Entries (${rows.length})`" flush>
        <DataTable
          :columns="columns"
          :rows="rows"
          :loading="statement.loading && !d.name"
          empty-icon="lucide-wallet"
          empty-title="No entries"
          empty-description="This statement has no commission entries."
        >
        </DataTable>
      </SectionCard>
    </div>

    <Dialog v-model="confirm.open" :title="confirm.title" :message="confirm.message" :icon="confirm.icon" :theme="confirm.theme">
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Not now" @click="confirm.open = false" />
          <Button variant="solid" :theme="confirm.theme" :label="confirm.label" :loading="confirm.busy" @click="runConfirm" />
        </div>
      </template>
    </Dialog>

    <Dialog v-model="payOpen" title="Mark as paid" size="md">
      <form id="mark-paid" class="space-y-4" @submit.prevent="submitPay">
        <FormControl v-model="pay.paid_on" label="Paid On" type="date" required />
        <FormControl v-model="pay.payment_reference" label="Payment Reference" placeholder="Bank or voucher reference" required />
      </form>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="payOpen = false" />
          <Button variant="solid" label="Mark Paid" type="submit" form="mark-paid" :loading="paying" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { Button, Dialog, FormControl, createResource, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import NumberCard from '@/components/NumberCard.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import DataTable from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import { callMethod } from '@/utils/api'
import { formatCurrency, formatDate, formatNumber, today } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })

const statement = createResource({
  url: 'field_sales_woocommerce.commission.api.get_commission_statement',
  params: { name: props.name },
  auto: true,
})
const d = computed(() => statement.data || {})
const errorText = computed(() => statement.error?.messages?.[0] || statement.error?.message || '')
const rows = computed(() => d.value.entries || [])

const money = (value) => (value == null || !d.value.name ? null : formatCurrency(value, d.value.currency))
const periodLabel = computed(() => (d.value.name ? `${formatDate(d.value.period_start)} – ${formatDate(d.value.period_end)}` : ''))

const details = computed(() => [
  { label: 'Sales Rep', value: d.value.full_name || d.value.sales_rep, route: d.value.sales_rep && { name: 'SalesRep', params: { name: d.value.sales_rep } } },
  { label: 'Period', value: periodLabel.value },
  { label: 'Status', value: d.value.status },
  { label: 'Approved By', value: d.value.approved_by, hide: !d.value.approved_by },
  { label: 'Paid On', value: d.value.paid_on ? formatDate(d.value.paid_on) : null },
  { label: 'Payment Reference', value: d.value.payment_reference },
])

const columns = [
  { key: 'platform_order', label: 'Store Order' },
  { key: 'entry_type', label: 'Type' },
  { key: 'beneficiary_type', label: 'Beneficiary' },
  { key: 'amount', label: 'Amount', align: 'right', format: (v, r) => formatCurrency(v, r.currency) },
  { key: 'reversed_amount', label: 'Reversed', align: 'right', format: (v, r) => (v ? formatCurrency(v, r.currency) : '—') },
  { key: 'net_amount', label: 'Net', align: 'right', class: 'font-medium', format: (v, r) => formatCurrency(v, r.currency) },
]

// Confirm dialog (same pattern as the order page)
const confirm = reactive({ open: false, title: '', message: '', label: '', icon: null, theme: 'gray', busy: false, action: null })
function ask(options) {
  Object.assign(confirm, { icon: null, theme: 'gray', ...options, open: true, busy: false })
}
async function runConfirm() {
  confirm.busy = true
  try {
    await confirm.action()
    confirm.open = false
  } catch {
    // callMethod already showed the error
  } finally {
    confirm.busy = false
  }
}

function askRegenerate() {
  ask({
    title: 'Regenerate statement?',
    message: 'The entries and totals are rebuilt from the rep\'s current commission entries.',
    label: 'Regenerate',
    action: async () => {
      await callMethod('field_sales_woocommerce.commission.statements.regenerate_statement', { name: props.name })
      toast.success('Statement regenerated')
      await statement.reload()
    },
  })
}

function askApprove() {
  ask({
    title: 'Approve statement?',
    message: `Approving locks ${d.value.full_name || d.value.sales_rep}'s statement and its entries. Later refunds are booked as adjustments.`,
    label: 'Approve',
    action: async () => {
      await callMethod('field_sales_woocommerce.commission.api.approve_statement', { name: props.name })
      toast.success('Statement approved')
      await statement.reload()
    },
  })
}

// Mark paid
const payOpen = ref(false)
const paying = ref(false)
const pay = reactive({ paid_on: today(), payment_reference: '' })
function openPay() {
  Object.assign(pay, { paid_on: today(), payment_reference: '' })
  payOpen.value = true
}
async function submitPay() {
  if (!pay.paid_on || !pay.payment_reference.trim()) {
    toast.error('Enter the payment date and reference')
    return
  }
  paying.value = true
  try {
    await callMethod('field_sales_woocommerce.commission.statements.mark_paid', {
      name: props.name,
      paid_on: pay.paid_on,
      payment_reference: pay.payment_reference.trim(),
    })
    toast.success('Statement marked as paid')
    payOpen.value = false
    await statement.reload()
  } catch {
    // callMethod already showed the error
  } finally {
    paying.value = false
  }
}
</script>
