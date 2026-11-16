<template>
  <!-- The wp-admin side menu (#adminmenu): dark column, blue current item with a notch
       pointing at the page, red count bubbles, "Collapse menu" folds it to icons. -->
  <nav
    aria-label="Main menu"
    class="flex shrink-0 flex-col bg-wp-menu text-wp-menu-text"
    :class="collapsed ? 'w-9' : 'w-sidebar'"
  >
    <ul class="flex-1 overflow-y-auto py-3">
      <li v-for="item in navItems" :key="item.label">
        <component
          :is="item.route ? 'RouterLink' : 'a'"
          v-bind="item.route ? { to: { name: item.route } } : { href: item.href }"
          class="wp-menu-item group relative flex min-h-[34px] items-center gap-2 py-1.5 text-[14px] leading-[18px] focus:outline-none"
          :class="[
            collapsed ? 'justify-center px-0' : 'pl-2 pr-2',
            isActive(item) ? 'bg-wp-blue text-white' : 'hover:bg-wp-menu-sub hover:text-[#72aee6] focus-visible:bg-wp-menu-sub focus-visible:text-[#72aee6]',
          ]"
          :aria-current="isActive(item) ? 'page' : undefined"
          :title="collapsed ? item.label : undefined"
        >
          <span
            :class="[item.icon, 'size-5 shrink-0', isActive(item) ? 'text-white' : 'text-wp-menu-muted group-hover:text-[#72aee6]']"
            aria-hidden="true"
          />
          <span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>
          <Tooltip v-if="countOf(item) && !collapsed" :text="item.countTooltip" :disabled="!item.countTooltip">
            <span
              class="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-[5px] text-[9px] font-semibold tabular-nums leading-none text-white"
              :class="item.countTheme === 'red' ? 'bg-wp-red' : 'bg-[#646970]'"
            >
              {{ countOf(item) }}
            </span>
          </Tooltip>
          <!-- The notch: a triangle in the page colour pointing at the current page -->
          <span
            v-if="isActive(item)"
            class="pointer-events-none absolute right-0 top-1/2 -mt-2 border-y-8 border-r-8 border-y-transparent border-r-desk-bg"
            aria-hidden="true"
          />
        </component>
      </li>
    </ul>

    <button
      type="button"
      class="flex h-[34px] shrink-0 items-center gap-2 text-[13px] text-wp-menu-muted hover:text-[#72aee6] focus:outline-none focus-visible:text-[#72aee6]"
      :class="collapsed ? 'justify-center' : 'pl-2'"
      :aria-label="collapsed ? 'Expand menu' : 'Collapse menu'"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
    >
      <span :class="collapsed ? 'lucide-circle-chevron-right' : 'lucide-circle-chevron-left'" class="size-5" aria-hidden="true" />
      <span v-if="!collapsed">Collapse menu</span>
    </button>
  </nav>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Tooltip } from 'frappe-ui'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS } from '@/utils/nav'
import { getPreference, setPreference } from '@/utils/storage'

const props = defineProps({
  /** frontend_api.get_sidebar_counts resource (AppLayout's useNavCounts). */
  counts: { type: Object, default: null },
})

const session = useSessionStore()
const route = useRoute()

const navItems = computed(() => NAV_ITEMS.filter((item) => session.canSee(item)))

function countOf(item) {
  return item.count ? props.counts?.data?.[item.count] || 0 : 0
}

function isActive(item) {
  // Detail pages (e.g. /retailers/RET-0001) keep their list's item highlighted; an item may
  // also own sibling pages (Status: Logs and Failures).
  if (!item.route) return false
  return [item.route, ...(item.also || [])].some((name) => route.name === name || route.meta.parent === name)
}

// Collapsed state survives reloads; it is a per-browser preference.
const collapsed = ref(getPreference('fs:sidebar-collapsed') === '1')
watch(collapsed, (v) => setPreference('fs:sidebar-collapsed', v ? '1' : '0'))
</script>
