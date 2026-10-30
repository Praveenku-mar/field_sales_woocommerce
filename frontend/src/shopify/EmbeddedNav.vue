<template>
  <!-- The Shopify admin's sidebar menu for this app (App Bridge <s-app-nav>). Renders nothing
       in the iframe itself. Same items and visibility rules as AppSidebar (session.canSee). -->
  <s-app-nav>
    <s-link :href="homeHref" rel="home">Dashboard</s-link>
    <s-link v-for="item in items" :key="item.route" :href="item.href">{{ item.label }}</s-link>
  </s-app-nav>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import { NAV_ITEMS } from '@/utils/nav'

const router = useRouter()
const session = useSessionStore()

const homeHref = computed(() => router.resolve({ name: 'Dashboard' }).href)
const items = computed(() =>
  NAV_ITEMS.filter((item) => item.route && item.route !== 'Dashboard' && session.canSee(item)).map((item) => ({
    ...item,
    href: router.resolve({ name: item.route }).href,
  })),
)

// App Bridge changes the iframe's history when a menu item is clicked; let vue-router follow
// it (it listens to popstate) and also handle App Bridge's navigation event if it sends one.
function onNavigate(event) {
  const href = event?.detail?.href || event?.target?.getAttribute?.('href')
  if (href && href !== router.currentRoute.value.fullPath) router.push(href.replace(router.options.history.base, '') || '/')
}
onMounted(() => document.addEventListener('shopify:navigate', onNavigate))
onBeforeUnmount(() => document.removeEventListener('shopify:navigate', onNavigate))
</script>
