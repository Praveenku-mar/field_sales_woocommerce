<template>
  <div>
    <PageHeader :title="d.retailer_name || name" :status="d.status">
      <template #meta><span class="text-ink-gray-5">{{ name }}</span></template>
      <template #actions>
        <Button
          v-if="session.isManager"
          label="Edit"
          icon-left="lucide-pencil"
          :disabled="!d.name"
          @click="editOpen = true"
        />
        <!-- Inside Shopify admin there is no location: check in / out happens in the standalone app -->
        <Button
          v-if="embedded && (d.status === 'In Progress' || (d.status === 'Planned' && isVisitDay))"
          label="Open in Field Sales"
          icon-left="lucide-external-link"
          variant="solid"
          @click="openStandalone(currentPath())"
        />
        <Button v-if="!embedded && d.status === 'Planned' && isOwnVisit && isVisitDay" label="Check In" icon-left="lucide-log-in" variant="solid" :loading="busy" @click="checkInOut('check_in')" />
        <template v-if="d.status === 'In Progress'">
          <Button label="New Order" icon-left="lucide-shopping-cart" @click="newOrderOpen = true" />
          <Button v-if="!embedded" label="Check Out" icon-left="lucide-log-out" variant="solid" :loading="busy" @click="startCheckOut" />
        </template>
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Visit not found" :description="errorText" /></div></div>

    <div v-else class="grid grid-cols-1 gap-5 px-5 pb-6 xl:grid-cols-3">
      <div class="space-y-5 xl:col-span-2">
        <div v-if="banner" class="rounded-4 px-4 py-3" :class="banner.class">{{ banner.text }}</div>

        <SectionCard title="Visit">
          <InfoGrid :items="details" :columns="3" />
        </SectionCard>

        <SectionCard title="Check-in Location">
          <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <InfoGrid :items="geo" :columns="1" />
            <MapEmbed
              :latitude="d.latitude || d.retailer_latitude"
              :longitude="d.longitude || d.retailer_longitude"
              :title="d.retailer_name"
              empty-description="Recorded when the rep checks in."
            />
          </div>
        </SectionCard>

        <SectionCard title="Orders from this Visit" flush>
          <template v-if="canOrder" #actions>
            <Button label="New Order" icon-left="lucide-plus" size="sm" @click="newOrderOpen = true" />
          </template>
          <DataTable :columns="orderColumns" :rows="orders.data || []" :loading="orders.loading" :row-route="(r) => ({ name: 'Order', params: { name: r.name } })" empty-icon="lucide-shopping-cart" empty-title="No orders from this visit">
            <template #cell-status="{ row }">
              <StatusPill :status="row.status" />
              <div v-if="row.short_items" class="mt-1 text-xs text-ink-red-6">{{ plural(row.short_items, 'item') }} over stock</div>
            </template>
          </DataTable>
        </SectionCard>
      </div>

      <div class="space-y-5">
        <SectionCard title="Time on Site">
          <div class="text-3xl font-semibold tabular-nums text-ink-gray-9">{{ onSite }}</div>
          <div class="mt-1 text-xs text-ink-gray-5">
            {{ d.check_in_time ? `In ${formatTime(d.check_in_time)}` : 'Not checked in' }}
            <template v-if="d.check_out_time"> · Out {{ formatTime(d.check_out_time) }}</template>
          </div>
        </SectionCard>
        <SectionCard title="Notes & Outcome">
          <InfoGrid :items="outcome" :columns="1" />
          <p v-if="d.notes" class="mt-3 whitespace-pre-line text-ink-gray-8">{{ d.notes }}</p>
        </SectionCard>
      </div>
    </div>

    <Dialog v-model="reasonOpen" title="No order taken" size="md">
      <div class="space-y-4">
        <p class="text-p-base text-ink-gray-7">No order was taken on this visit. Tell us why before checking out.</p>
        <FormControl v-model="reason.no_order_reason" type="select" label="Reason" :options="REASONS" />
        <FormControl
          v-model="reason.no_order_remarks"
          type="textarea"
          :label="reason.no_order_reason === 'Other' ? 'Details (required)' : 'Details'"
          placeholder="What happened?"
        />
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="reasonOpen = false" />
          <Button variant="solid" label="Check Out" :disabled="!reasonValid" :loading="busy" @click="checkOutWithReason" />
        </div>
      </template>
    </Dialog>

    <!-- Managers only: session.isManager gates the Edit button above. -->
    <VisitDialog v-model="editOpen" :visit="name" @updated="doc.reload()" />
    <NewOrderDialog v-model="newOrderOpen" :retailer="d.retailer" :visit="name" :sales-rep="d.sales_rep" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Dialog, FormControl, createDocumentResource, createResource, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import MapEmbed from '@/components/MapEmbed.vue'
import DataTable from '@/components/DataTable.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import VisitDialog from '@/components/VisitDialog.vue'
import NewOrderDialog from '@/components/NewOrderDialog.vue'
import { useSessionStore } from '@/stores/session'
import { callMethod, getPosition } from '@/utils/api'
import { formatCurrency, formatDate, formatTime, plural, today } from '@/utils/format'
import { isEmbedded, openStandalone } from '@/shopify/embedded'

const session = useSessionStore()
const embedded = isEmbedded()
// This page's path without Shopify's query (shop, host, id_token): opens the standalone app.
const currentPath = () => window.location.pathname
// Only the assigned rep checks in (the server enforces it too). A Sales Rep is named
// after its user, so the rep's name is the user's email.
const isOwnVisit = computed(() => !!d.value.sales_rep && d.value.sales_rep === session.user)
// Check-in is only open on the visit's own date (the server checks too).
const isVisitDay = computed(() => d.value.visit_date === today())
const editOpen = ref(false)
const newOrderOpen = ref(false)

const props = defineProps({ name: { type: String, required: true } })

const doc = createDocumentResource({ doctype: 'Visit', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

// Drafts count too: an order waiting on stock is still the order taken on this visit.
const orders = createResource({ url: 'field_sales_woocommerce.api.get_visit_orders', params: { visit: props.name }, auto: true })
const canOrder = computed(() => ['In Progress', 'Completed'].includes(d.value.status))

const details = computed(() => [
  { label: 'Customer', value: d.value.retailer_name, route: d.value.retailer && { name: 'Retailer', params: { name: d.value.retailer } } },
  { label: 'City', value: d.value.city },
  { label: 'Sales Rep', value: d.value.sales_rep_name },
  { label: 'Date', value: formatDate(d.value.visit_date) },
  { label: 'Purpose', value: d.value.visit_purpose },
  { label: 'Order Taken', value: d.value.order_taken ? 'Yes' : 'No', hide: d.value.status !== 'Completed' },
])
const geo = computed(() => [
  {
    label: 'Geofence',
    value: !d.value.check_in_time
      ? 'Not checked in'
      : d.value.within_geofence
        ? `Inside (${Math.round(d.value.distance_from_retailer_m || 0)} m from the customer)`
        : `Outside — ${Math.round(d.value.distance_from_retailer_m || 0)} m from the customer`,
  },
  { label: 'GPS accuracy', value: d.value.location_accuracy_m ? `± ${Math.round(d.value.location_accuracy_m)} m` : null },
  { label: 'Map shows', value: d.value.latitude ? 'Where the rep checked in' : 'The customer (no check-in yet)' },
])
const outcome = computed(() => [
  { label: 'Order Taken', value: d.value.order_taken ? 'Yes' : 'No' },
  { label: 'No Order Reason', value: d.value.no_order_reason, hide: !!d.value.order_taken || !d.value.no_order_reason },
  { label: 'Reason Details', value: d.value.no_order_remarks, hide: !!d.value.order_taken || !d.value.no_order_remarks },
])

const onSite = computed(() => {
  if (d.value.status === 'Completed') return `${d.value.time_on_site_min || 0} min`
  if (d.value.status === 'In Progress' && d.value.check_in_time) {
    const start = new Date(String(d.value.check_in_time).replace(' ', 'T').slice(0, 19))
    return `${Math.max(0, Math.round((Date.now() - start) / 60000))} min`
  }
  return '—'
})

const banner = computed(() => {
  if (d.value.status === 'Planned' && d.value.visit_date < today()) {
    return {
      text: `Planned for ${formatDate(d.value.visit_date)} and not checked in. Check-in is closed; a manager can move the visit to today.`,
      class: 'bg-surface-amber-1 text-ink-amber-7',
    }
  }
  if (d.value.status === 'Planned' && isOwnVisit.value && d.value.visit_date > today()) {
    return { text: `Check-in opens on ${formatDate(d.value.visit_date)}.`, class: 'bg-surface-gray-2 text-ink-gray-7' }
  }
  if (d.value.check_in_time && !d.value.within_geofence) {
    return { text: `Checked in ${Math.round(d.value.distance_from_retailer_m || 0)} m away from the customer.`, class: 'bg-surface-amber-1 text-ink-amber-7' }
  }
  return null
})

const orderColumns = [
  { key: 'name', label: 'Order' },
  { key: 'order_date', label: 'Date', format: formatDate },
  { key: 'items', label: 'Items', align: 'right', format: (v) => plural(v || 0, 'item') },
  { key: 'grand_total', label: 'Total', align: 'right', format: (v) => formatCurrency(v) },
  { key: 'status', label: 'Status' },
]

// Check out: without an order on the visit, the rep first says why none was taken.
const REASONS = ['', 'Stock Available', 'Owner Absent', 'Price Issue', 'Shop Closed', 'Other']
const reasonOpen = ref(false)
const reason = ref({ no_order_reason: '', no_order_remarks: '' })
const reasonValid = computed(
  () => !!reason.value.no_order_reason && (reason.value.no_order_reason !== 'Other' || !!reason.value.no_order_remarks.trim()),
)
async function startCheckOut() {
  const current = await orders.reload()
  if ((current || []).some((o) => o.status !== 'Cancelled')) return checkInOut('check_out')
  reason.value = { no_order_reason: d.value.no_order_reason || '', no_order_remarks: d.value.no_order_remarks || '' }
  reasonOpen.value = true
}
async function checkOutWithReason() {
  await checkInOut('check_out', reason.value)
  reasonOpen.value = false
}

// Check in / out: browser GPS, then the same server checks as the desk button.
const busy = ref(false)
async function checkInOut(action, extra = {}) {
  busy.value = true
  try {
    const coords = await getPosition().catch((error) => {
      toast.error(error.message)
      throw error
    })
    const result = await callMethod(`field_sales_woocommerce.api.${action}`, {
      visit: props.name,
      latitude: coords.latitude,
      longitude: coords.longitude,
      accuracy: coords.accuracy,
      ...extra,
    })
    if (result.warning) toast.warning(result.warning)
    else toast.success(action === 'check_in' ? 'Checked in' : `Checked out after ${result.time_on_site_min} min`)
    await doc.reload()
  } finally {
    busy.value = false
  }
}
</script>
