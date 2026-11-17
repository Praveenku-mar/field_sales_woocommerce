<template>
  <!-- wp-admin .postbox: white box, title bar with a hairline under it. -->
  <section class="fs-card">
    <header v-if="title || $slots.actions" class="flex min-h-[36px] items-center justify-between gap-2 border-b border-wp-divider px-3 py-1.5">
      <h2 class="text-[14px] font-semibold leading-[1.4] text-ink-gray-8">{{ title }}</h2>
      <div class="flex items-center gap-2"><slot name="actions" /></div>
    </header>
    <div :class="flush ? '' : 'p-3'"><slot /></div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: null },
  /** No inner padding (for tables and lists that draw their own). */
  flush: { type: Boolean, default: false },
})
</script>
