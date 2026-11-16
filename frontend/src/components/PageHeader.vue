<template>
  <!-- wp-admin page heading: `h1.wp-heading-inline` with its actions right beside it
       (`.page-title-action`), then the page's own content below. -->
  <div class="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 pb-3 pt-4">
    <div class="flex min-w-0 items-center gap-2.5">
      <h1 class="truncate text-[23px] font-normal leading-[1.3] text-ink-gray-8">{{ title }}</h1>
      <StatusPill v-if="status" :status="status" />
      <slot name="meta" />
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>

<script setup>
import StatusPill from './StatusPill.vue'

defineProps({
  title: { type: String, required: true },
  /** Shown as a StatusPill next to the title. */
  status: { type: String, default: null },
})
</script>
