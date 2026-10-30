<template>
  <Dialog v-model="open" :title="isEdit ? 'Edit Visit' : 'New Visit'" size="lg" @after-leave="resetForm">
    <div class="space-y-6">
      <section>
        <h3 class="text-base font-medium text-ink-gray-9">Visit Details</h3>
        <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <Combobox
            v-model="form.retailer"
            :options="customerOptions"
            trigger="button"
            label="Customer"
            required
            placeholder="Select customer"
            empty-text="No customers found"
            :disabled="started"
          />
          <Combobox
            v-model="form.sales_rep"
            :options="repOptions"
            trigger="button"
            label="Sales Rep"
            required
            placeholder="Select sales rep"
            empty-text="No sales reps found"
            description="Visits are visible through their Sales Rep"
            :disabled="started"
          />
          <DatePicker v-model="form.visit_date" label="Visit Date" required />
          <Select v-model="form.visit_purpose" :options="PURPOSES" label="Visit Purpose" placeholder="Select purpose" />
        </div>
        <p v-if="started" class="mt-3 text-sm text-ink-gray-5">
          The customer and sales rep can no longer be changed: the visit has started and its check-in was measured against them.
        </p>
      </section>

      <section class="border-t border-desk-border pt-6">
        <h3 class="text-base font-medium text-ink-gray-9">Notes</h3>
        <div class="mt-4">
          <Textarea v-model="form.notes" placeholder="Anything the rep should know before the visit" :rows="3" />
        </div>
        <p v-if="!isEdit" class="mt-4 text-sm text-ink-gray-5">
          The visit is planned; check in from the visit page when the rep reaches the customer.
        </p>
      </section>
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="busy" @click="open = false" />
        <Button variant="solid" :label="isEdit ? 'Save' : 'Create'" :loading="busy" :disabled="!valid" @click="submit" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button,
  Combobox,
  DatePicker,
  Dialog,
  Select,
  Textarea,
  createListResource,
  createResource,
  toast,
} from 'frappe-ui'
import { callMethod } from '@/utils/api'
import { useSessionStore } from '@/stores/session'
import { today } from '@/utils/format'

const PURPOSES = ['Order Taking', 'Collection', 'Merchandising', 'Complaint', 'Follow-up']

const props = defineProps({
  /** Visit name to edit; absent (or null) means create. */
  visit: { type: String, default: null },
})
const emit = defineEmits(['updated'])

const open = defineModel({ type: Boolean, default: false })
const isEdit = computed(() => !!props.visit)

const session = useSessionStore()
const router = useRouter()

// Customers for the visit (active ones), searchable in the combobox.
const customers = createListResource({
  doctype: 'Retailer',
  fields: ['name', 'retailer_name'],
  filters: { status: 'Active' },
  orderBy: 'retailer_name asc',
  pageLength: 100,
})
const customerOptions = computed(() =>
  (customers.data || []).map((r) => ({ label: r.retailer_name || r.name, value: r.name })),
)

// Sales reps for the visit.
const reps = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name'],
  orderBy: 'full_name asc',
  pageLength: 100,
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))

// Planning fields only: status defaults to Planned; check-in times, location
// and outcome fields are captured on the visit itself.
const FIELDS = ['retailer', 'sales_rep', 'visit_date', 'visit_purpose', 'notes']
const emptyForm = () => ({
  retailer: null,
  sales_rep: null,
  visit_date: today(),
  visit_purpose: '',
  notes: '',
})
const form = reactive(emptyForm())
const loaded = ref(null)
const busy = ref(false)
// Once check-in happened, the visit's customer/rep are frozen (its distance
// was measured against them).
const started = ref(false)

function resetForm() {
  Object.assign(form, emptyForm())
  loaded.value = null
  started.value = false
}

// The doc being edited (prefill source).
const editDoc = createResource({
  url: 'frappe.client.get',
  makeParams: () => ({ doctype: 'Visit', name: props.visit }),
})

watch(open, async (value) => {
  if (!value) return
  if (!customers.data && !customers.loading) customers.fetch()
  if (props.visit) {
    try {
      const doc = await editDoc.fetch()
      for (const field of FIELDS) {
        form[field] = field === 'retailer' || field === 'sales_rep' ? doc[field] || null : doc[field] ?? ''
      }
      loaded.value = { ...form }
      started.value = !!doc.check_in_time
    } catch {
      toast.error('Could not load the visit')
      open.value = false
    }
  } else {
    // A rep plans their own visits; the server would assign them anyway.
    form.sales_rep = form.sales_rep || session.salesRep?.name || null
  }
})

const valid = computed(() => form.retailer && form.sales_rep && form.visit_date)

async function submit() {
  busy.value = true
  try {
    if (isEdit.value) {
      await saveChanges()
    } else {
      await createVisit()
    }
  } catch {
    // callMethod already toasted the error.
  } finally {
    busy.value = false
  }
}

async function createVisit() {
  const doc = { doctype: 'Visit' }
  for (const field of FIELDS) {
    const value = form[field]
    if (value !== '' && value != null) doc[field] = value
  }
  const inserted = await callMethod('frappe.client.insert', { doc })
  toast.success(`Visit ${inserted.name} planned`)
  open.value = false
  router.push({ name: 'Visit', params: { name: inserted.name } })
}

async function saveChanges() {
  // Only what changed; '' clears a field server-side.
  const fieldname = {}
  for (const field of FIELDS) {
    if (String(form[field] ?? '') !== String(loaded.value?.[field] ?? '')) {
      fieldname[field] = form[field] === null ? '' : form[field]
    }
  }
  if (!Object.keys(fieldname).length) {
    open.value = false
    return
  }
  const saved = await callMethod('frappe.client.set_value', {
    doctype: 'Visit',
    name: props.visit,
    fieldname,
  })
  toast.success(`Visit ${saved.name || props.visit} updated`)
  open.value = false
  loaded.value = { ...form }
  emit('updated')
}
</script>
