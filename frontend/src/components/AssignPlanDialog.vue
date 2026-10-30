<template>
  <Dialog v-model="open" title="Assign commission plan" size="md">
    <form id="assign-plan" class="space-y-4" @submit.prevent="save">
      <p class="text-sm text-ink-gray-6">
        Commission plan for <span class="font-medium text-ink-gray-9">{{ rep?.full_name || rep?.sales_rep }}</span>
      </p>
      <div v-if="error" class="rounded-4 bg-surface-red-2 px-3 py-2 text-sm text-ink-red-7" role="alert">{{ error }}</div>
      <FormControl v-model="form.commission_plan" type="select" label="Plan" :options="planOptions" />
      <Switch v-model="form.commission_active" label="Commission Active" />
    </form>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="saving" @click="open = false" />
        <Button variant="solid" type="submit" form="assign-plan" label="Save" :loading="saving" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, FormControl, Switch, createResource, toast } from 'frappe-ui'

/** Set a rep's plan and commission switch. `rep` is a team row; `plans` the plans to pick from. Emits `saved`. */
const props = defineProps({ rep: { type: Object, default: null }, plans: { type: Array, default: () => [] } })
const emit = defineEmits(['saved'])
const open = defineModel({ type: Boolean, default: false })

const form = reactive({ commission_plan: '', commission_active: false })
const error = ref('')
const saving = ref(false)

// Active plans, plus the rep's current one even if it has since been deactivated.
const planOptions = computed(() => [
  { label: 'No plan', value: '' },
  ...props.plans
    .filter((p) => p.is_active || p.name === props.rep?.commission_plan)
    .map((p) => ({ label: p.is_active ? p.name : `${p.name} (inactive)`, value: p.name })),
])

watch(open, (value) => {
  if (!value) return
  error.value = ''
  form.commission_plan = props.rep?.commission_plan || ''
  form.commission_active = !!props.rep?.commission_active
})

async function save() {
  error.value = ''
  saving.value = true
  try {
    await createResource({ url: 'field_sales_woocommerce.commission.api.assign_commission_plan' }).submit({
      sales_rep: props.rep.sales_rep,
      commission_plan: form.commission_plan || null,
      commission_active: form.commission_active ? 1 : 0,
    })
    toast.success('Commission plan updated')
    open.value = false
    emit('saved')
  } catch (e) {
    error.value = String(e?.messages?.[0] || e?.message || 'Could not save').replace(/<[^>]*>/g, '')
  } finally {
    saving.value = false
  }
}
</script>
