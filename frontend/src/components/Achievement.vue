<template>
  <div v-if="pct != null" class="flex items-center gap-2">
    <div class="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-gray-2">
      <div class="h-full rounded-full" :class="barClass" :style="{ width: Math.min(pct, 100) + '%' }" />
    </div>
    <span class="w-12 text-right text-xs tabular-nums text-ink-gray-6">{{ Math.round(pct) }}%</span>
  </div>
  <span v-else class="text-xs text-ink-gray-4">No target set</span>
</template>

<script setup>
import { computed } from 'vue'

/** Target achievement as a bar: green from 100%, amber from 70%, else red. */
const props = defineProps({ pct: { type: Number, default: null } })
const barClass = computed(() =>
  props.pct >= 100 ? 'bg-surface-green-3' : props.pct >= 70 ? 'bg-surface-amber-3' : 'bg-surface-red-5',
)
</script>
