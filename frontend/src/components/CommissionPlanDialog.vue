<template>
  <Dialog v-model="open" :title="isEdit ? `Edit plan: ${form.plan_name}` : 'New Commission Plan'" size="3xl">
    <form id="commission-plan" class="space-y-6" @submit.prevent="save">
      <div v-if="error" class="rounded-4 bg-surface-red-2 px-3 py-2 text-sm text-ink-red-7" role="alert">{{ error }}</div>

      <section class="grid grid-cols-1 gap-4 md:grid-cols-2">
        <FormControl v-model="form.plan_name" label="Plan Name" required :disabled="isEdit" />
        <FormControl v-model="form.rule_type" type="select" label="Rule Type" :options="RULE_TYPES" />
        <FormControl v-model="form.commission_on" type="select" label="Commission On" :options="COMMISSION_ON" />
        <div class="flex items-end pb-1"><Switch v-model="form.is_active" label="Active" /></div>
      </section>

      <section class="space-y-4">
        <h3 class="text-base font-medium text-ink-gray-9">Rule</h3>

        <FormControl
          v-if="form.rule_type === 'Flat %'"
          v-model.number="form.flat_rate"
          label="Flat Rate %"
          type="number"
          min="0"
          step="any"
          required
        />

        <template v-else-if="form.rule_type === 'Product Rate'">
          <FormControl
            v-model.number="form.default_rate"
            label="Default Rate %"
            type="number"
            min="0"
            step="any"
            description="Used when no row below matches"
            class="md:w-1/2"
          />
          <div class="space-y-2">
            <div class="text-sm text-ink-gray-6">Rates (the most specific match wins: product, then collection, then tag)</div>
            <div v-for="(row, i) in form.rates" :key="i" class="grid grid-cols-[150px_minmax(0,1fr)_90px_32px] items-start gap-2">
              <FormControl v-model="row.match_type" type="select" :options="MATCH_TYPES" aria-label="Match type" @update:model-value="clearMatch(row)" />
              <LinkField
                v-if="row.match_type === 'Product'"
                v-model="row.product"
                class="w-full"
                doctype="Platform Product"
                :label-of="productLabel"
                description-field="sku"
                :fields="['product_title', 'variant_title', 'sku']"
                :search-fields="['product_title', 'variant_title', 'sku', 'barcode']"
                :filters="{ status: 'Active' }"
                :label="row.product_label || row.product"
                placeholder="Select product"
                @select="(p) => (row.product_label = productLabel(p))"
              />
              <FormControl
                v-else
                v-model="row.match_value"
                :placeholder="row.match_type === 'Tag' ? 'Tag' : 'Collection or product type'"
                :aria-label="row.match_type"
              />
              <FormControl v-model.number="row.rate" type="number" min="0" step="any" placeholder="Rate %" aria-label="Rate %" />
              <Button variant="ghost" icon="lucide-trash-2" aria-label="Remove row" @click="form.rates.splice(i, 1)" />
            </div>
            <Button label="Add Row" icon-left="lucide-plus" @click="form.rates.push(newRate())" />
          </div>
        </template>

        <template v-else-if="form.rule_type === 'Tiered Slab'">
          <FormControl
            v-model="form.slab_mode"
            type="select"
            label="Slab Mode"
            :options="SLAB_MODES"
            description="Whole Amount: one rate for all sales. Marginal: each slab pays its own rate on the part inside it."
            class="md:w-1/2"
          />
          <div class="space-y-2">
            <div class="text-sm text-ink-gray-6">Slabs (by the month's total sales; To = 0 means no limit)</div>
            <div v-for="(row, i) in form.slabs" :key="i" class="grid grid-cols-[repeat(3,minmax(0,1fr))_32px] items-start gap-2">
              <FormControl v-model.number="row.from_amount" type="number" min="0" placeholder="From" aria-label="From amount" />
              <FormControl v-model.number="row.to_amount" type="number" min="0" placeholder="To (0 = no limit)" aria-label="To amount" />
              <FormControl v-model.number="row.rate" type="number" min="0" step="any" placeholder="Rate %" aria-label="Rate %" />
              <Button variant="ghost" icon="lucide-trash-2" aria-label="Remove row" @click="form.slabs.splice(i, 1)" />
            </div>
            <Button label="Add Slab" icon-left="lucide-plus" @click="addSlab" />
          </div>
        </template>

        <FormControl
          v-else-if="form.rule_type === 'Per Order'"
          v-model.number="form.per_order_amount"
          label="Amount per Order"
          type="number"
          min="0"
          step="any"
          required
          class="md:w-1/2"
        />
      </section>

      <section class="grid grid-cols-1 gap-4 md:grid-cols-3">
        <FormControl
          v-model.number="form.penalty_factor"
          label="Discount Penalty Factor"
          type="number"
          min="0"
          max="1"
          step="any"
          description="0 to 1; 0 = no penalty"
        />
        <FormControl
          v-model.number="form.override_levels"
          label="Override Levels"
          type="number"
          min="0"
          step="1"
          description="Managers above the rep who earn an override"
        />
        <FormControl v-model.number="form.override_pct" label="Override %" type="number" min="0" step="any" />
        <FormControl v-model="form.description" type="textarea" label="Description" class="md:col-span-3" />
      </section>
    </form>

    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="saving" @click="open = false" />
        <Button variant="solid" type="submit" form="commission-plan" :label="isEdit ? 'Save' : 'Create'" :loading="saving" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Button, Dialog, FormControl, Switch, createResource, toast } from 'frappe-ui'
import LinkField from '@/components/LinkField.vue'

/** Create a Commission Plan, or edit `plan` (a row of get_commission_plans). Emits `saved` with the saved plan. */
const props = defineProps({ plan: { type: Object, default: null } })
const emit = defineEmits(['saved'])
const open = defineModel({ type: Boolean, default: false })

const options = (...values) => values.map((value) => ({ label: value, value }))
const RULE_TYPES = options('Flat %', 'Product Rate', 'Tiered Slab', 'Per Order')
const COMMISSION_ON = options('Paid', 'Fulfilled')
const MATCH_TYPES = options('Product', 'Collection/Product Type', 'Tag')
const SLAB_MODES = options('Whole Amount', 'Marginal')

const newRate = () => ({ match_type: 'Product', product: null, match_value: '', rate: null })
const emptyForm = () => ({
  name: null,
  plan_name: '',
  is_active: true,
  rule_type: 'Flat %',
  commission_on: 'Paid',
  flat_rate: null,
  default_rate: null,
  rates: [newRate()],
  slab_mode: 'Whole Amount',
  slabs: [{ from_amount: 0, to_amount: 0, rate: null }],
  per_order_amount: null,
  penalty_factor: 0,
  override_levels: 0,
  override_pct: 0,
  description: '',
})
const form = reactive(emptyForm())
const isEdit = computed(() => !!form.name)
const error = ref('')

watch(open, (value) => {
  if (!value) return
  error.value = ''
  const p = props.plan
  Object.assign(form, emptyForm())
  if (!p) return
  Object.assign(form, {
    ...p,
    is_active: !!p.is_active,
    rates: (p.rates || []).map((r) => ({ ...r })),
    slabs: (p.slabs || []).map((r) => ({ ...r })),
    description: p.description || '',
  })
})

const productLabel = (row) => [row.product_title, row.variant_title].filter(Boolean).join(' - ') || row.sku || row.name || row.product
const clearMatch = (row) => {
  row.product = null
  row.match_value = ''
}
function addSlab() {
  const last = form.slabs[form.slabs.length - 1]
  form.slabs.push({ from_amount: last?.to_amount || 0, to_amount: 0, rate: null })
}

const stripHtml = (text) => String(text || '').replace(/<[^>]*>/g, '')
const saving = ref(false)
async function save() {
  error.value = ''
  saving.value = true
  try {
    const doc = {
      ...form,
      is_active: form.is_active ? 1 : 0,
      rates: form.rates.map(({ match_type, product, match_value, rate }) => ({ match_type, product, match_value, rate })),
    }
    const saved = await createResource({ url: 'field_sales_woocommerce.commission.api.save_commission_plan' }).submit({ doc })
    toast.success(`Plan ${saved.name} saved`)
    open.value = false
    emit('saved', saved)
  } catch (e) {
    error.value = stripHtml(e?.messages?.[0] || e?.message) || 'Could not save the plan'
  } finally {
    saving.value = false
  }
}
</script>
