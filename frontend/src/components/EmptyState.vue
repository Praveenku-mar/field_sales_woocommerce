<template>
  <div class="flex flex-col items-center justify-center px-6 py-16 text-center">
    <div class="flex size-10 items-center justify-center rounded-full bg-surface-gray-2">
      <span :class="[icon, 'size-5 text-ink-gray-5']" />
    </div>
    <div class="mt-3 text-base font-medium text-ink-gray-8">{{ title }}</div>
    <div v-if="description" class="mt-1 max-w-sm text-ink-gray-5">{{ description }}</div>
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
