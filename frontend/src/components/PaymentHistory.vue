<template>
  <!-- The order's payment history (Payment Transaction): links, store checkout attempts,
       payments and refunds, newest first. Ids copy on click. -->
  <div>
    <h3 class="mb-1 text-sm font-semibold text-ink-gray-8">Payment history</h3>
    <ul class="divide-y divide-outline-gray-1">
      <li v-for="t in shown" :key="t.name" class="flex items-start justify-between gap-3 py-2 text-sm">
        <div class="min-w-0">
          <div class="text-ink-gray-8">{{ title(t) }}</div>
          <div class="text-xs text-ink-gray-5">
            {{ t.occurred_on ? formatDateTime(t.occurred_on) : '' }}
            <button
              v-if="idOf(t)"
              type="button"
              class="ml-1 font-mono hover:underline"
              :title="`Copy ${idOf(t)}`"
              @click="copy(idOf(t))"
            >
              {{ idOf(t) }}
            </button>
          </div>
          <div v-if="t.status === 'Failed' && t.error" class="text-xs text-ink-red-6">{{ t.error }}</div>
        </div>
        <div class="flex shrink-0 flex-col items-end gap-1">
          <StatusPill :status="t.status" />
          <span v-if="t.amount" class="tabular-nums text-ink-gray-8">{{ t.transaction_type === 'Refund' ? '−' : '' }}{{ formatCurrency(t.amount) }}</span>
        </div>
      </li>
    </ul>
    <button
      v-if="transactions.length > LIMIT"
      type="button"
      class="mt-1 text-xs text-ink-blue-link hover:underline"
      @click="all = !all"
    >
      {{ all ? 'Show less' : `Show all (${transactions.length})` }}
    </button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { toast } from 'frappe-ui'
import StatusPill from './StatusPill.vue'
import { formatCurrency, formatDateTime } from '@/utils/format'

const props = defineProps({
  /** get_order_payment_info's `transactions`, newest first. */
  transactions: { type: Array, required: true },
})

const LIMIT = 4
const all = ref(false)
const shown = computed(() => (all.value ? props.transactions : props.transactions.slice(0, LIMIT)))

function title(t) {
  if (t.transaction_type === 'Payment Link') return 'Payment link'
  if (t.transaction_type === 'Checkout Order') return 'Store checkout'
  if (t.transaction_type === 'Refund') return t.reference ? `Refund · ${t.reference}` : 'Refund'
  if (t.source === 'Manual') return `Payment · ${t.method || 'manual'}`
  return t.method ? `Payment · ${t.method}` : 'Payment'
}

const idOf = (t) => t.razorpay_refund_id || t.razorpay_payment_id || t.razorpay_payment_link_id || t.razorpay_order_id || (t.source === 'Manual' ? t.reference : null)

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text)
    toast.success('Copied')
  } catch {
    toast.error('Could not copy')
  }
}
</script>
