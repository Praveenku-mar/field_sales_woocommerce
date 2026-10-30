<template>
  <Dialog v-model="open" title="New Order" size="lg" @after-leave="resetForm">
    <div class="space-y-6">
      <section>
        <h3 class="text-base font-medium text-ink-gray-9">Order Details</h3>
        <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <Combobox
            v-model="form.retailer"
            :options="customerOptions"
            trigger="button"
            label="Customer"
            required
            placeholder="Select customer"
            empty-text="No customers found"
            :disabled="!!visit"
          />
          <Combobox
            v-model="form.sales_rep"
            :options="repOptions"
            trigger="button"
            label="Sales Rep"
            required
            placeholder="Select sales rep"
            empty-text="No sales reps found"
          />
          <DatePicker v-model="form.order_date" label="Order Date" required />
          <Select
            v-if="multipleLocations"
            v-model="form.location_id"
            :options="locationOptions"
            label="Location"
            required
          />
        </div>
        <p v-if="visit" class="mt-3 text-sm text-ink-gray-5">
          The order is attached to this visit; its customer is fixed.
        </p>
      </section>

      <section class="border-t border-desk-border pt-6">
        <p class="text-sm text-ink-gray-5">
          A draft order is created without items — add products and submit it on the order page.
        </p>
      </section>
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="creating" @click="open = false" />
        <Button variant="solid" label="Create Draft" :loading="creating" :disabled="!valid" @click="create" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Combobox, DatePicker, Dialog, Select, createListResource, toast } from 'frappe-ui'
import { callMethod } from '@/utils/api'
import { useSessionStore } from '@/stores/session'
import { useSellLocations } from '@/composables/useSellLocations'
import { today } from '@/utils/format'

const props = defineProps({
  /** Prefill/fix the customer (e.g. opened from a customer or visit page). */
  retailer: { type: String, default: null },
  /** Attach the order to this visit (customer becomes fixed). */
  visit: { type: String, default: null },
  /** Prefill the sales rep (e.g. the visit's rep). */
  salesRep: { type: String, default: null },
})

const open = defineModel({ type: Boolean, default: false })

const session = useSessionStore()
const router = useRouter()
// The order sells from one location; with a single one there is nothing to pick.
const { options: locationOptions, multiple: multipleLocations, defaultLocation } = useSellLocations()

// Customers (active ones), searchable in the combobox.
const customers = createListResource({
  doctype: 'Retailer',
  fields: ['name', 'retailer_name'],
  filters: { status: 'Active' },
  orderBy: 'retailer_name asc',
  pageLength: 100,
})
const customerOptions = computed(() => {
  const options = (customers.data || []).map((r) => ({ label: r.retailer_name || r.name, value: r.name }))
  // The fixed customer may be inactive or not yet loaded; keep it selectable.
  if (form.retailer && !options.some((o) => o.value === form.retailer)) {
    options.unshift({ label: form.retailer, value: form.retailer })
  }
  return options
})

// Sales reps.
const reps = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name'],
  orderBy: 'full_name asc',
  pageLength: 100,
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))

const emptyForm = () => ({
  retailer: null,
  sales_rep: null,
  order_date: today(),
  location_id: null,
})
const form = reactive(emptyForm())

function resetForm() {
  Object.assign(form, emptyForm())
}

watch(open, (value) => {
  if (!value) return
  if (!customers.data && !customers.loading) customers.fetch()
  form.retailer = form.retailer || props.retailer || null
  form.sales_rep = form.sales_rep || props.salesRep || session.salesRep?.name || null
  form.location_id = form.location_id || defaultLocation.value?.location_id || null
})

const valid = computed(() => form.retailer && form.sales_rep && form.order_date)

const creating = ref(false)
async function create() {
  creating.value = true
  try {
    // Items are added on the order page; the server inserts the header-only draft.
    const inserted = await callMethod('field_sales_woocommerce.api.create_order', {
      retailer: form.retailer,
      sales_rep: form.sales_rep,
      order_date: form.order_date,
      visit: props.visit || '',
      location_id: form.location_id || '',
    })
    toast.success(`Draft ${inserted.name} created`)
    open.value = false
    router.push({ name: 'Order', params: { name: inserted.name } })
  } catch {
    // callMethod already toasted the error.
  } finally {
    creating.value = false
  }
}
</script>
