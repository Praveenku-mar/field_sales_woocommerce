<template>
  <section class="fs-card">
    <header v-if="title || $slots.actions" class="flex items-center justify-between gap-2 border-b border-desk-border px-4 py-2.5">
      <h2 class="text-base font-medium text-ink-gray-9">{{ title }}</h2>
      <div class="flex items-center gap-2"><slot name="actions" /></div>
    </header>
    <div :class="flush ? '' : 'p-4'"><slot /></div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: null },
  /** No inner padding (for tables and lists that draw their own). */
  flush: { type: Boolean, default: false },
})
</script>
