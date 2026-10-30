<template>
  <Dialog v-model="open" title="New Sales Rep" size="lg" @after-leave="resetForm">
    <div class="space-y-6">
      <section>
        <h3 class="text-base font-medium text-ink-gray-9">Details</h3>
        <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <div>
            <TextInput
              v-model="form.user"
              label="User (Email)"
              required
              type="email"
              placeholder="rep@example.com"
              @blur="checkUser"
            />
            <p class="mt-1.5 text-xs" :class="userHint.class" aria-live="polite">{{ userHint.text }}</p>
          </div>
          <TextInput
            v-model="form.full_name"
            label="Full Name"
            :required="isNewUser"
            :placeholder="isNewUser ? 'Name for the new user' : 'Defaults to the user\'s name'"
          />
          <TextInput v-model="form.mobile" label="Mobile" type="tel" />
          <TextInput v-model="form.territory" label="Territory" />
          <Combobox
            v-model="form.reports_to"
            :options="repOptions"
            trigger="button"
            label="Reports To"
            placeholder="Select manager"
            empty-text="No sales reps found"
          />
          <TextInput v-model="form.monthly_target" label="Monthly Target" type="number" />
          <TextInput
            v-model="form.max_discount_pct"
            label="Max Discount % per Order"
            type="number"
            min="0"
            max="100"
            placeholder="Empty = no discount"
          />
          <FormControl
            v-if="isNewUser"
            v-model="form.send_welcome_email"
            type="checkbox"
            label="Email an invite to set a password"
            class="md:col-span-2"
          />
        </div>
      </section>
    </div>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="creating" @click="open = false" />
        <Button variant="solid" label="Create" :loading="creating" :disabled="!canCreate" @click="create" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Combobox, Dialog, FormControl, TextInput, createListResource, createResource, debounce, toast } from 'frappe-ui'
import { callMethod } from '@/utils/api'

const open = defineModel({ type: Boolean, default: false })

const router = useRouter()

// Managers for "Reports To" (everyone but the new rep, once loaded).
const reps = createListResource({
  doctype: 'Sales Rep',
  fields: ['name', 'full_name'],
  orderBy: 'full_name asc',
  pageLength: 100,
  auto: true,
})
const repOptions = computed(() => (reps.data || []).map((r) => ({ label: r.full_name || r.name, value: r.name })))

const emptyForm = () => ({
  user: '',
  full_name: '',
  mobile: '',
  territory: '',
  reports_to: null,
  monthly_target: '',
  max_discount_pct: '',
  send_welcome_email: true,
})
const form = reactive(emptyForm())

function resetForm() {
  Object.assign(form, emptyForm())
  lookup.reset()
}

// Is the email already a user? A new one is created with the rep (Field Sales Rep role).
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const email = computed(() => form.user.trim().toLowerCase())
const lookup = createResource({
  url: 'field_sales_woocommerce.api.get_user_for_sales_rep',
  makeParams: () => ({ email: email.value }),
  // Answers for an email the user has since changed are ignored.
  transform: (data) => ({ ...data, email: email.value }),
})
const checkUser = () => EMAIL.test(email.value) && lookup.data?.email !== email.value && lookup.submit()
watch(email, debounce(() => checkUser(), 400))

const known = computed(() => (lookup.data?.email === email.value ? lookup.data : null))
const isNewUser = computed(() => known.value?.exists === false)
const userHint = computed(() => {
  if (email.value && !EMAIL.test(email.value)) return { text: 'Enter a valid email address.', class: 'text-ink-red-5' }
  if (lookup.loading) return { text: 'Checking…', class: 'text-ink-gray-5' }
  if (!known.value) return { text: 'An existing user, or a new email to create one.', class: 'text-ink-gray-5' }
  if (known.value.sales_rep) return { text: 'This user is already a Sales Rep.', class: 'text-ink-red-5' }
  if (known.value.exists) return { text: `Existing user: ${known.value.full_name || email.value}`, class: 'text-ink-green-7' }
  return { text: 'New user: an account with the Field Sales Rep role will be created.', class: 'text-ink-amber-7' }
})
const canCreate = computed(
  () => EMAIL.test(email.value) && !known.value?.sales_rep && !(isNewUser.value && !form.full_name.trim()),
)

const creating = ref(false)
async function create() {
  creating.value = true
  try {
    const params = { ...form, user: email.value, send_welcome_email: form.send_welcome_email ? 1 : 0 }
    const inserted = await callMethod('field_sales_woocommerce.api.create_sales_rep', params)
    toast.success(inserted.user_created ? `User and Sales Rep ${inserted.name} created` : `Sales Rep ${inserted.name} created`)
    open.value = false
    router.push({ name: 'SalesRep', params: { name: inserted.name } })
  } catch {
    // callMethod already toasted the error (e.g. already a Sales Rep).
  } finally {
    creating.value = false
  }
}
</script>
