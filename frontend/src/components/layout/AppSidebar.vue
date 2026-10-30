<template>
  <Sidebar v-model:collapsed="collapsed" width="224px" class="border-r border-outline-gray-2">
    <SidebarHeader
      title="Field Sales"
      :subtitle="session.store.name || 'No store connected'"
      logo="/assets/field_sales/images/logo.svg"
      class="px-2"
    />

    <div class="flex flex-1 flex-col gap-1.5 overflow-y-auto px-2 pb-2">
      <!-- Staff tools: a customer login has no search or notifications. -->
      <SidebarItem
        v-if="!session.isRetailer"
        label="Search"
        icon="lucide-search"
        style="height: 2.5rem"
        @click="$emit('open-search')"
      >
        <template #prefix><span class="lucide-search size-6 text-ink-gray-6" /></template>
        <template #suffix>
          <kbd class="mr-2 text-xs text-ink-gray-4">Ctrl K</kbd>
        </template>
      </SidebarItem>
      <SidebarItem
        v-if="!session.isRetailer"
        label="Notification"
        icon="lucide-bell"
        style="height: 2.5rem"
        :route="{ name: 'Notifications' }"
        :active="route.name === 'Notifications'"
      >
        <template #prefix><span class="lucide-bell size-6 text-ink-gray-6" /></template>
        <template v-if="counts.data?.unread_notifications" #suffix>
          <span class="mr-2 text-xs font-medium tabular-nums text-ink-red-5">{{ counts.data.unread_notifications }}</span>
        </template>
      </SidebarItem>

      <div v-if="!session.isRetailer" class="h-6 shrink-0" />

      <SidebarItem
        v-for="item in navItems"
        :key="item.label"
        :label="item.label"
        style="height: 2.5rem"
        :route="item.route && { name: item.route }"
        :href="item.href"
        :active="isActive(item)"
      >
        <template #prefix><span :class="[item.icon, 'size-6 text-ink-gray-6']" /></template>
        <span class="truncate text-base leading-tighter">{{ item.label }}</span>
        <template v-if="countOf(item)" #suffix>
          <Tooltip :text="item.countTooltip" :disabled="!item.countTooltip">
            <span
              class="mr-2 text-xs tabular-nums"
              :class="item.countTheme === 'red' ? 'font-medium text-ink-red-5' : 'text-ink-gray-5'"
            >
              {{ countOf(item) }}
            </span>
          </Tooltip>
        </template>
      </SidebarItem>
    </div>

    <div class="flex flex-col gap-0.5 border-t border-outline-gray-2 p-2">
      <UserMenu :collapsed="collapsed" />
      <SidebarCollapseToggle />
    </div>
  </Sidebar>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Sidebar, SidebarCollapseToggle, SidebarHeader, SidebarItem, Tooltip, createResource } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS } from '@/utils/nav'
import { getPreference, setPreference } from '@/utils/storage'
import UserMenu from './UserMenu.vue'

defineEmits(['open-search'])

const session = useSessionStore()
const route = useRoute()

const navItems = computed(() => NAV_ITEMS.filter((item) => session.canSee(item)))

// A customer login shows no counts (its nav has none), so it never fetches or polls them.
const counts = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_sidebar_counts',
  auto: !session.isRetailer,
})

function countOf(item) {
  return item.count ? counts.data?.[item.count] || 0 : 0
}

function isActive(item) {
  // Detail pages (e.g. /retailers/RET-0001) keep their list's item highlighted.
  return !!item.route && (route.name === item.route || route.meta.parent === item.route)
}

// Collapsed state survives reloads; it is a per-browser preference.
const collapsed = ref(getPreference('fs:sidebar-collapsed') === '1')
watch(collapsed, (v) => setPreference('fs:sidebar-collapsed', v ? '1' : '0'))

// Counts change as people work; refresh them when moving between pages.
watch(
  () => route.name,
  () => !session.isRetailer && counts.reload(),
)
// The Notifications page announces read/unread changes made without leaving it.
const reloadCounts = () => counts.reload()
// There is no realtime connection, so poll while the tab is in view to pick up new notifications.
let poll = null
onMounted(() => {
  window.addEventListener('fs:notifications-changed', reloadCounts)
  poll = setInterval(() => {
    if (!session.isRetailer && document.visibilityState === 'visible') counts.reload()
  }, 60000)
})
onBeforeUnmount(() => {
  window.removeEventListener('fs:notifications-changed', reloadCounts)
  clearInterval(poll)
})
</script>
