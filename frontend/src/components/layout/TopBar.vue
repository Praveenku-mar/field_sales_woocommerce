<template>
  <!-- The wp-admin toolbar (#wpadminbar): dark strip across the top with the site name,
       search and notifications on the left and "Howdy, <name>" on the right. -->
  <!-- Inside wp-admin, WordPress draws the real toolbar and menu: this shrinks to a light row
       with search and notifications only. -->
  <header
    class="flex shrink-0 items-center gap-1 px-2 text-[13px]"
    :class="inWpAdmin ? 'h-9 justify-end border-b border-wp-divider bg-white text-ink-gray-7' : 'h-topbar bg-wp-menu text-wp-menu-text md:h-adminbar'"
  >
    <RouterLink
      v-if="!inWpAdmin"
      :to="{ name: homeRoute }"
      class="flex h-full min-w-0 items-center gap-2 px-2 hover:bg-wp-menu-sub hover:text-[#72aee6] focus:outline-none focus-visible:bg-wp-menu-sub"
    >
      <img :src="LOGO" alt="" class="size-5 shrink-0 rounded-sm" />
      <span class="truncate font-normal">{{ session.store.name || 'Agamsale' }}</span>
    </RouterLink>

    <button
      v-if="!session.isRetailer"
      type="button"
      class="flex h-full items-center gap-1.5 px-2 focus:outline-none"
      :class="itemClass"
      aria-label="Search"
      @click="$emit('open-search')"
    >
      <span class="lucide-search size-4" aria-hidden="true" />
      <span class="hidden md:inline">Search</span>
      <kbd class="hidden font-sans text-[11px] text-wp-menu-muted md:inline">Ctrl K</kbd>
    </button>

    <RouterLink
      v-if="!session.isRetailer"
      :to="{ name: 'Notifications' }"
      class="flex h-full items-center gap-1 px-2 focus:outline-none"
      :class="[itemClass, route.name === 'Notifications' && (inWpAdmin ? 'text-wp-blue' : 'bg-wp-menu-sub text-[#72aee6]')]"
      :aria-label="unread ? `Notifications, ${unread} unread` : 'Notifications'"
    >
      <span class="lucide-bell size-4" aria-hidden="true" />
      <span
        v-if="unread"
        class="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-wp-red px-[5px] text-[9px] font-semibold leading-none text-white"
      >
        {{ unread }}
      </span>
    </RouterLink>

    <div v-if="!inWpAdmin" class="flex-1" />
    <!-- Inside a store admin's frame there is no account menu: its own login signs the app in. -->
    <UserMenu v-if="!embedded" />
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import UserMenu from './UserMenu.vue'
import { isTokenSession } from '@/wordpress/embed'
import { useSessionStore } from '@/stores/session'

const props = defineProps({
  /** frontend_api.get_sidebar_counts resource (AppLayout's useNavCounts). */
  counts: { type: Object, default: null },
  /** In the WordPress plugin's frame. */
  inWpAdmin: { type: Boolean, default: false },
})
defineEmits(['open-search'])

// Served by Frappe at runtime, not bundled by Vite.
const LOGO = '/assets/field_sales_woocommerce/images/agamsale-logo.png'
const embedded = isTokenSession()
const session = useSessionStore()
const route = useRoute()

const homeRoute = computed(() => (session.isRetailer ? 'MyOrders' : 'Dashboard'))
const itemClass = computed(() =>
  props.inWpAdmin
    ? 'hover:text-wp-blue focus-visible:text-wp-blue'
    : 'text-wp-menu-text hover:bg-wp-menu-sub hover:text-[#72aee6] focus-visible:bg-wp-menu-sub',
)
const unread = computed(() => props.counts?.data?.unread_notifications || 0)
</script>
