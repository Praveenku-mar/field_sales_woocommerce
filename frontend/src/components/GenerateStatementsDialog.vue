<template>
  <Dialog v-model="open" title="Generate statements" size="md" @after-leave="reset">
    <form id="generate-statements" class="space-y-4" @submit.prevent="generate">
      <p class="text-sm text-ink-gray-6">
        Drafts a statement for each rep with open commission entries up to the end of the month. Reps who already have
        one for the month are skipped.
      </p>
      <div v-if="error" class="rounded-4 bg-surface-red-2 px-3 py-2 text-sm text-ink-red-7" role="alert">{{ error }}</div>
      <FormControl v-model="form.month" type="select" label="Month" :options="months" />
      <div class="space-y-1.5">
        <div class="text-sm text-ink-gray-6">Sales Rep</div>
        <LinkField
          v-model="form.sales_rep"
          class="w-full"
          doctype="Sales Rep"
          title-field="full_name"
          description-field="territory"
          :search-fields="['full_name', 'name', 'territory']"
          placeholder="All commission-active reps"
        />
        <button v-if="form.sales_rep" type="button" class="text-xs text-ink-gray-6 underline" @click="form.sales_rep = null">
          Clear (all reps)
        </button>
      </div>
    </form>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="busy" @click="open = false" />
        <Button variant="solid" type="submit" form="generate-statements" label="Generate" :loading="busy" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { Button, Dialog, FormControl, createResource, toast } from 'frappe-ui'
import LinkField from '@/components/LinkField.vue'
import { plural } from '@/utils/format'

/** Manager-only: draft the statements of a month now. Emits `generated` with the result. */
const emit = defineEmits(['generated'])
const open = defineModel({ type: Boolean, default: false })

// The last 12 months, newest first, from last month (the usual one to generate).
const months = Array.from({ length: 12 }, (_, i) => {
  const d = new Date()
  d.setDate(1)
  d.setMonth(d.getMonth() - 1 - i)
  return {
    label: d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' }),
    value: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
  }
})
const emptyForm = () => ({ month: months[0].value, sales_rep: null })
const form = reactive(emptyForm())
const error = ref('')
const busy = ref(false)
const reset = () => {
  Object.assign(form, emptyForm())
  error.value = ''
}

async function generate() {
  error.value = ''
  busy.value = true
  try {
    const res = await createResource({ url: 'field_sales_woocommerce.commission.api.generate_statements' }).submit({
      month: form.month,
      sales_rep: form.sales_rep || undefined,
    })
    const made = res.created.length
    const skipped = res.skipped.length
    const text = `${plural(made, 'statement')} created` + (skipped ? `, ${skipped} skipped` : '')
    if (made) toast.success(text)
    else toast.info(text)
    open.value = false
    emit('generated', res)
  } catch (e) {
    error.value = String(e?.messages?.[0] || e?.message || 'Could not generate').replace(/<[^>]*>/g, '')
  } finally {
    busy.value = false
  }
}
</script>
