<template>
  <!-- wp-admin `.nav-tab-wrapper`: folder tabs sitting on a hairline (WooCommerce → Settings). -->
  <nav class="flex flex-wrap items-end gap-x-1 border-b border-wp-border px-5" :aria-label="label">
    <component
      :is="tab.route ? 'RouterLink' : 'button'"
      v-for="tab in tabs"
      :key="tab.value ?? tab.route?.name"
      v-bind="tab.route ? { to: tab.route } : { type: 'button' }"
      class="-mb-px whitespace-nowrap border px-2.5 py-[5px] text-[14px] font-semibold leading-[1.71] focus:outline-none focus-visible:ring-2 focus-visible:ring-wp-blue"
      :class="isCurrent(tab) ? 'border-wp-border border-b-desk-bg bg-desk-bg text-ink-gray-8' : 'border-wp-border bg-[#dcdcde] text-[#50575e] hover:bg-white hover:text-[#3c434a]'"
      :aria-current="isCurrent(tab) ? 'page' : undefined"
      @click="!tab.route && (model = tab.value)"
    >
      {{ tab.label }}
    </component>
  </nav>
</template>

<script setup>
import { useRoute } from 'vue-router'

defineProps({
  /** [{ label, value }] for a v-model, or [{ label, route }] for links between pages. */
  tabs: { type: Array, required: true },
  label: { type: String, default: 'Secondary menu' },
})
const model = defineModel({ type: [String, Number], default: null })
const route = useRoute()

function isCurrent(tab) {
  if (tab.route) return route.name === tab.route.name
  return model.value === tab.value
}
</script>
