<template>
  <div>
    <PageHeader :title="name" :status="d.name ? statusOf(d) : null">
      <template #actions>
        <Button
          v-if="d.docstatus === 1"
          label="Download Invoice"
          icon-left="lucide-download"
          :href="invoiceUrl"
          target="_blank"
        />
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5">
      <div class="fs-card">
        <EmptyState
          icon="lucide-circle-alert"
          title="Order not found"
          :description="errorText"
        />
      </div>
    </div>

    <div v-else class="grid grid-cols-1 gap-5 px-5 pb-6 xl:grid-cols-3">
      <div class="space-y-5 xl:col-span-2">
        <SectionCard title="Details">
          <InfoGrid :items="details" :columns="3" />
        </SectionCard>

        <SectionCard title="Items" flush>
          <DataTable
            :columns="itemColumns"
            :rows="d.items || []"
            :loading="doc.get.loading"
            empty-icon="lucide-package"
            empty-title="No items"
          >
            <template #cell-product="{ row }">
              <div class="font-medium text-ink-gray-9">{{ row.product_title }}</div>
              <div v-if="row.variant_title" class="text-xs text-ink-gray-5">{{ row.variant_title }}</div>
            </template>
          </DataTable>
        </SectionCard>

        <SectionCard title="Totals">
          <InfoGrid :items="totals" :columns="2" />
        </SectionCard>
      </div>

      <div class="space-y-5">
        <!-- Payment: read-only for the customer; links open where the money is taken. -->
        <div v-if="d.docstatus === 1" class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-credit-card size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">Payment</h2>
            <StatusPill :status="pay.payment_status || 'Unpaid'" />
          </div>
          <dl class="divide-y divide-outline-gray-1 px-4">
            <div v-for="item in paymentRows" :key="item.label" class="flex items-center justify-between gap-3 py-2.5">
              <dt class="shrink-0 text-ink-gray-6">{{ item.label }}</dt>
              <dd class="min-w-0 truncate text-right text-ink-gray-8">
                <a
                  v-if="item.href"
                  :href="item.href"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="inline-flex items-center gap-1 text-ink-blue-6 hover:underline"
                >
                  {{ item.value }}<span class="lucide-external-link size-3.5" />
                </a>
                <template v-else>{{ item.value }}</template>
              </dd>
            </div>
          </dl>
          <div v-if="payActions" class="space-y-2 p-4 pt-1">
            <Button
              v-if="payNowUrl"
              class="w-full"
              variant="solid"
              label="Pay Now"
              icon-left="lucide-external-link"
              :href="payNowUrl"
              target="_blank"
            />
            <Button
              v-else-if="storeCheckout"
              class="w-full"
              variant="solid"
              label="Pay in Store Checkout"
              icon-left="lucide-external-link"
              :href="pay.store_checkout_url"
              target="_blank"
            />
            <Button
              v-if="canRefreshPayment"
              class="w-full"
              label="Refresh Payment Status"
              icon-left="lucide-refresh-cw"
              :loading="refreshing"
              @click="refreshPayment"
            />
          </div>
        </div>

        <SectionCard v-if="d.remarks" title="Remarks">
          <p class="whitespace-pre-line text-ink-gray-8">{{ d.remarks }}</p>
        </SectionCard>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, createDocumentResource, createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import { onDocEvent } from '@/composables/useRealtime'
import { callMethod } from '@/utils/api'
import { formatCurrency, formatDate } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })

const doc = createDocumentResource({ doctype: 'Field Order', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

/** Payment state only exists on submitted orders; the server re-checks read access. */
const payResource = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_order_payment_info',
  method: 'GET',
  makeParams: () => ({ field_order: props.name }),
  auto: true,
})
const pay = computed(() => payResource.data || {})

/** What a customer tracks: drafts and cancellations stay as such. */
function statusOf(order) {
  if (order.docstatus === 0) return 'Draft'
  if (order.docstatus === 2) return 'Cancelled'
  return order.payment_status || 'Unpaid'
}

const details = computed(() => [
  { label: 'Order Date', value: formatDate(d.value.order_date) },
  { label: 'Delivery Date', value: formatDate(d.value.delivery_date), hide: !d.value.delivery_date },
  { label: 'Invoice No', value: d.value.invoice_no || '—' },
])

const itemColumns = [
  { key: 'product', label: 'Product' },
  { key: 'qty', label: 'Qty', align: 'right' },
  { key: 'rate', label: 'Rate', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'amount', label: 'Amount', align: 'right', format: (v) => formatCurrency(v) },
]

const totals = computed(() => {
  const o = d.value
  return [
    { label: 'Net Total', value: formatCurrency(o.net_total) },
    { label: 'Discount', value: formatCurrency(o.discount_amount), hide: !flt(o.discount_amount) },
    { label: 'Tax', value: formatCurrency(o.tax_amount), hide: !flt(o.tax_amount) },
    { label: 'Grand Total', value: formatCurrency(o.grand_total) },
    { label: 'Amount Paid', value: formatCurrency(o.amount_paid), hide: !flt(o.amount_paid) },
    { label: 'Balance Due', value: formatCurrency((flt(o.grand_total) || 0) - (flt(o.amount_paid) || 0)), hide: !flt(o.amount_paid) },
  ]
})

function flt(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

const storeCheckout = computed(
  () =>
    !!pay.value.store_checkout_url &&
    !['Paid', 'Partially Paid'].includes(pay.value.payment_status) &&
    d.value.docstatus === 1,
)

// The link to press once nothing has been paid against it yet.
const payNowUrl = computed(() =>
  d.value.docstatus === 1 &&
  pay.value.razorpay_payment_link_url &&
  !['Paid', 'Partially Paid'].includes(pay.value.payment_status)
    ? pay.value.razorpay_payment_link_url
    : null,
)

const payActions = computed(
  () => d.value.docstatus === 1 && (!!payNowUrl.value || storeCheckout.value || canRefreshPayment.value),
)

const canRefreshPayment = computed(
  () => d.value.docstatus === 1 && !!pay.value.razorpay_payment_link_id && pay.value.payment_status !== 'Paid',
)

const paymentRows = computed(() => {
  const p = pay.value
  const rows = []
  if (flt(p.amount_paid)) rows.push({ label: 'Amount Paid', value: formatCurrency(p.amount_paid) })
  if (p.razorpay_payment_link_url)
    rows.push({ label: 'Payment Link', value: 'Open to pay', href: p.razorpay_payment_link_url })
  else if (storeCheckout.value) rows.push({ label: 'Store Checkout', value: 'Pay online', href: p.store_checkout_url })
  if (p.razorpay_payment_id) rows.push({ label: 'Payment ID', value: p.razorpay_payment_id })
  if (p.paid_on) rows.push({ label: 'Paid On', value: formatDate(p.paid_on) })
  return rows.length ? rows : [{ label: 'Status', value: p.payment_status || 'Unpaid' }]
})

const invoiceUrl = computed(
  () =>
    `/api/method/frappe.utils.print_format.download_pdf?doctype=Field%20Order&name=${encodeURIComponent(
      props.name,
    )}&format=Field%20Sales%20Invoice`,
)

const refreshing = ref(false)
async function refreshPayment() {
  refreshing.value = true
  try {
    await callMethod('field_sales_woocommerce.payments.razorpay.refresh_payment_status', { field_order: props.name })
    await Promise.all([payResource.reload(), doc.reload()])
  } catch {
    // callMethod already toasted the error.
  } finally {
    refreshing.value = false
  }
}

// A payment link paid elsewhere flips the status here without a reload.
onDocEvent('Field Order', props.name, 'field_order_payment_updated', () => {
  payResource.reload()
  doc.reload()
})
</script>
