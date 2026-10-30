<template>
  <!-- Phones (< 768px): the sidebar is hidden and these four pages sit in a bottom tab bar. -->
  <nav
    aria-label="Main"
    class="grid shrink-0 border-t border-outline-gray-2 bg-surface-base pb-[env(safe-area-inset-bottom)] md:hidden"
    :style="{ gridTemplateColumns: `repeat(${Math.max(items.length, 1)}, minmax(0, 1fr))` }"
  >
    <RouterLink
      v-for="item in items"
      :key="item.route"
      :to="{ name: item.route }"
      class="flex min-h-14 flex-col items-center justify-center gap-1 text-xs focus:outline-none focus-visible:bg-surface-gray-2"
      :class="isActive(item) ? 'font-medium text-ink-gray-9' : 'text-ink-gray-5'"
      :aria-current="isActive(item) ? 'page' : undefined"
    >
      <span :class="item.icon" class="size-5" aria-hidden="true" />
      {{ item.label }}
    </RouterLink>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { TAB_ITEMS } from '@/utils/nav'

const route = useRoute()
const session = useSessionStore()

// Staff get their four pages; a customer login gets its My Orders tab.
const items = computed(() => TAB_ITEMS.filter((item) => session.canSee(item)))

function isActive(item) {
  return route.name === item.route || route.meta.parent === item.route
}
</script>
