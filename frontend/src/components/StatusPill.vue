<template>
  <!-- WooCommerce's order-status mark (`mark.order-status`): a tinted, slightly rounded chip. -->
  <span
    class="inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded px-2 py-0.5 text-[13px] leading-[1.6]"
    :class="STYLES[tone]"
  >
    <span v-if="icon" :class="[icon, 'size-3 shrink-0']" aria-hidden="true" />
    <span class="truncate">{{ label || status }}</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: null },
  /** Icon class (e.g. lucide-circle-check) rendered before the label. */
  icon: { type: String, default: null },
})

// WooCommerce order status colours: pending (grey), on-hold (amber), processing (green),
// completed (blue-grey), failed/cancelled (red).
const STYLES = {
  pending: 'bg-[#e5e5e5] text-[#454545]',
  hold: 'bg-[#f8dda7] text-[#94660c]',
  processing: 'bg-[#c6e1c6] text-[#5b841b]',
  completed: 'bg-[#c8d7e1] text-[#2e4453]',
  failed: 'bg-[#eba3a3] text-[#761919]',
}

// Sync, payment and document states used across Field Order, Retailer, Visit, Sales Rep and Sync Log.
const TONES = {
  Synced: 'processing',
  Success: 'processing',
  Completed: 'processing',
  Active: 'processing',
  Earned: 'processing',
  Paid: 'processing',
  Pending: 'hold',
  Retrying: 'hold',
  'In Progress': 'hold',
  'Partially Paid': 'hold',
  'Link Expired': 'hold',
  Planned: 'completed',
  Approved: 'completed',
  'Link Sent': 'completed',
  Failed: 'failed',
  'Out of Stock': 'failed',
  Cancelled: 'failed',
  Reversed: 'failed',
  'Link Cancelled': 'failed',
  'Partially Refunded': 'hold',
  Refunded: 'failed',
  // Payment history (Payment Transaction)
  Captured: 'processing',
  Processed: 'processing',
  Authorized: 'hold',
  Expired: 'hold',
  Created: 'completed',
  Draft: 'pending',
  Unpaid: 'pending',
  Inactive: 'pending',
}
const tone = computed(() => TONES[props.status] || 'pending')
</script>
