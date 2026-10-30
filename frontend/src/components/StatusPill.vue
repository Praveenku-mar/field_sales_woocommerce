<template>
  <Badge :label="label || status" :theme="theme" variant="subtle" size="md">
    <template v-if="icon" #prefix><span :class="[icon, 'size-3']" /></template>
  </Badge>
</template>

<script setup>
import { computed } from 'vue'
import { Badge } from 'frappe-ui'

const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: null },
  /** Icon class (e.g. lucide-circle-check) rendered before the label. */
  icon: { type: String, default: null },
})

// Sync, payment and document states used across Field Order, Retailer, Visit, Sales Rep and Sync Log.
const THEMES = {
  Synced: 'green',
  Success: 'green',
  Completed: 'green',
  Active: 'green',
  Pending: 'amber',
  Retrying: 'amber',
  'In Progress': 'amber',
  Planned: 'blue',
  Failed: 'red',
  'Out of Stock': 'red',
  Cancelled: 'red',
  Earned: 'green',
  Approved: 'blue',
  Paid: 'green',
  Reversed: 'red',
  Draft: 'gray',
  Unpaid: 'gray',
  'Link Sent': 'blue',
  'Partially Paid': 'amber',
  'Link Expired': 'amber',
  'Link Cancelled': 'red',
  Inactive: 'gray',
}
const theme = computed(() => THEMES[props.status] || 'gray')
</script>
