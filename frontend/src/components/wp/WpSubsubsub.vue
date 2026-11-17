<template>
  <!-- wp-admin `ul.subsubsub`: "All (42) | Drafts (3) | Failed (1)" status filters. -->
  <ul class="flex flex-wrap items-center text-[13px] text-[#646970]" aria-label="Filter by status">
    <li v-for="(option, i) in options" :key="option.value" class="whitespace-nowrap">
      <button
        type="button"
        class="focus:outline-none focus-visible:underline"
        :class="model === option.value ? 'font-semibold text-ink-gray-8' : 'wp-link'"
        :aria-current="model === option.value ? 'true' : undefined"
        @click="model = option.value"
      >
        {{ option.label }}
        <span v-if="counts?.[option.value] != null" class="font-normal text-[#50575e]">({{ counts[option.value] }})</span>
      </button>
      <span v-if="i < options.length - 1" class="px-1.5 text-[#a7aaad]" aria-hidden="true">|</span>
    </li>
  </ul>
</template>

<script setup>
defineProps({
  /** [{ label, value }] */
  options: { type: Array, required: true },
  /** { [value]: number } — shown in brackets after each label when known. */
  counts: { type: Object, default: null },
})
const model = defineModel({ type: [String, Number], default: null })
</script>
