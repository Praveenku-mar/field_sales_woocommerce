<template>
  <!-- Quiet, like a wp-admin list table's "No items found." row. -->
  <div class="flex flex-col items-center justify-center px-6 py-12 text-center">
    <span :class="[icon, 'size-8 text-[#a7aaad]']" aria-hidden="true" />
    <div class="mt-3 text-[14px] font-semibold text-ink-gray-8">{{ title }}</div>
    <div v-if="description" class="mt-1 max-w-sm text-wp-muted">{{ description }}</div>
    <div v-if="$slots.default" class="mt-4"><slot /></div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: null },
  icon: { type: String, default: 'lucide-inbox' },
})
</script>
