<template>
  <div v-if="label" class="flex items-center gap-1.5 rounded-full bg-surface-gray-3 py-1 pl-3 pr-1.5 text-sm text-ink-gray-8">
    <span class="lucide-filter size-3.5 text-ink-gray-5" />
    <span>Showing: <b class="font-medium">{{ label }}</b></span>
    <button class="flex size-5 items-center justify-center rounded-full hover:bg-surface-gray-4" title="Show all" @click="clear">
      <span class="lucide-x size-3.5" />
    </button>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'

defineProps({ label: { type: String, default: null } })

const route = useRoute()
const router = useRouter()

/** Drop ?preset= (and the tab it chose), showing the page's normal list. */
function clear() {
  const query = { ...route.query }
  delete query.preset
  delete query.tab
  router.replace({ query })
}
</script>
