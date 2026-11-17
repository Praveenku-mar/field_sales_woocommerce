<template>
  <!-- A manager's refund of the order's Razorpay payment (payments/refunds.py). The request id
       is made when the dialog opens, so a retried click is not refunded twice. -->
  <Dialog v-model="open" title="Refund payment" size="md">
    <form id="refund-form" class="space-y-4" @submit.prevent="submit">
      <p class="text-p-base text-ink-gray-7">
        The money goes back to the customer through Razorpay. Up to {{ formatCurrency(maxAmount) }} can be refunded.
      </p>
      <FormControl v-model="amount" type="number" label="Amount" :min="0" :max="maxAmount" step="0.01" required />
      <FormControl v-model="reason" type="textarea" label="Reason" placeholder="Why is this refunded?" />
      <p v-if="tooMuch" class="text-sm text-ink-red-6">The amount is more than what can be refunded.</p>
    </form>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="busy" @click="open = false" />
        <Button variant="solid" theme="red" label="Refund" type="submit" form="refund-form" :loading="busy" :disabled="!valid" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Button, Dialog, FormControl, toast } from 'frappe-ui'
import { callMethod } from '@/utils/api'
import { formatCurrency } from '@/utils/format'

const props = defineProps({
  fieldOrder: { type: String, required: true },
  /** What is left to refund (get_order_payment_info's refundable_amount). */
  maxAmount: { type: Number, default: 0 },
})
const emit = defineEmits(['refunded'])
const open = defineModel({ type: Boolean, default: false })

const amount = ref(0)
const reason = ref('')
const busy = ref(false)
let requestId = ''

watch(open, (value) => {
  if (!value) return
  amount.value = props.maxAmount
  reason.value = ''
  requestId = `rf-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
})

const tooMuch = computed(() => Number(amount.value) > props.maxAmount + 0.005)
const valid = computed(() => Number(amount.value) > 0 && !tooMuch.value)

async function submit() {
  if (!valid.value || busy.value) return
  busy.value = true
  try {
    const result = await callMethod('field_sales_woocommerce.payments.refunds.refund_order_payment', {
      field_order: props.fieldOrder,
      amount: Number(amount.value),
      reason: reason.value,
      request_id: requestId,
    })
    toast.success(result?.status === 'pending' ? 'Refund started' : 'Refunded')
    open.value = false
    emit('refunded', result)
  } catch {
    // callMethod shows the server's message; the dialog stays open.
  } finally {
    busy.value = false
  }
}
</script>
