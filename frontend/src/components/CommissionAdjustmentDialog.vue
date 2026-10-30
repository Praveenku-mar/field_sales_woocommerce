<template>
  <Dialog v-model="open" title="New adjustment" size="md" @after-leave="reset">
    <form id="commission-adjustment" class="space-y-4" @submit.prevent="save">
      <div v-if="error" class="rounded-4 bg-surface-red-2 px-3 py-2 text-sm text-ink-red-7" role="alert">{{ error }}</div>
      <div class="space-y-1.5">
        <div class="text-sm text-ink-gray-6">Sales Rep <span class="text-ink-red-4">*</span></div>
        <LinkField
          v-model="form.sales_rep"
          class="w-full"
          doctype="Sales Rep"
          title-field="full_name"
          description-field="territory"
          :search-fields="['full_name', 'name', 'territory']"
          :filters="{ active: 1 }"
          placeholder="Select sales rep"
        />
      </div>
      <FormControl
        v-model.number="form.amount"
        label="Amount"
        type="number"
        step="any"
        required
        description="Positive adds to the commission, negative deducts"
      />
      <FormControl v-model="form.posting_date" label="Posting Date" type="date" required />
      <FormControl v-model="form.beneficiary_type" type="select" label="Beneficiary" :options="BENEFICIARIES" />
      <FormControl v-model="form.reason" type="textarea" label="Reason" required placeholder="Why is this adjustment needed?" />
    </form>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="saving" @click="open = false" />
        <Button variant="solid" type="submit" form="commission-adjustment" label="Add Adjustment" :loading="saving" :disabled="!ready" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { Button, Dialog, FormControl, createResource, toast } from 'frappe-ui'
import LinkField from '@/components/LinkField.vue'
import { today } from '@/utils/format'

/** Manager-only: add a manual adjustment to a rep's commission. Emits `saved`. */
const emit = defineEmits(['saved'])
const open = defineModel({ type: Boolean, default: false })

const BENEFICIARIES = [
  { label: 'Rep', value: 'Rep' },
  { label: 'Manager Override', value: 'Manager Override' },
]
const emptyForm = () => ({ sales_rep: null, amount: null, posting_date: today(), beneficiary_type: 'Rep', reason: '' })
const form = reactive(emptyForm())
const error = ref('')
const saving = ref(false)

const ready = computed(() => !!form.sales_rep && !!Number(form.amount) && !!form.reason.trim() && !!form.posting_date)
const reset = () => {
  Object.assign(form, emptyForm())
  error.value = ''
}

async function save() {
  if (!ready.value) return
  error.value = ''
  saving.value = true
  try {
    await createResource({ url: 'field_sales_woocommerce.commission.api.create_adjustment' }).submit({ ...form })
    toast.success('Adjustment added')
    open.value = false
    emit('saved')
  } catch (e) {
    error.value = String(e?.messages?.[0] || e?.message || 'Could not save').replace(/<[^>]*>/g, '')
  } finally {
    saving.value = false
  }
}
</script>
