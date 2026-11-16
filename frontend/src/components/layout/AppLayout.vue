<template>
  <!-- wp-admin frame: the toolbar across the top, the side menu on the left, the page on
       the grey background. Phones swap the side menu for the bottom tab bar. -->
  <div class="flex h-full flex-col overflow-hidden bg-desk-bg">
    <TopBar :counts="counts" :in-wp-admin="inWpAdmin" @open-search="searchOpen = true" />
    <div class="flex min-h-0 flex-1">
      <!-- Inside the Shopify admin the admin's own sidebar carries the navigation; inside
           wp-admin the plugin's Agamsale submenus do (wordpress/embed.js announceRoutes). -->
      <EmbeddedNav v-if="embedded" />
      <AppSidebar v-else-if="!inWpAdmin" class="hidden md:flex" :counts="counts" />
      <div class="flex min-w-0 flex-1 flex-col">
        <main class="flex-1 overflow-y-auto">
          <slot />
        </main>
        <MobileTabBar v-if="!embedded && !inWpAdmin" />
      </div>
    </div>
    <CommandPalette v-model="searchOpen" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppSidebar from './AppSidebar.vue'
import TopBar from './TopBar.vue'
import MobileTabBar from './MobileTabBar.vue'
import CommandPalette from './CommandPalette.vue'
import EmbeddedNav from '@/shopify/EmbeddedNav.vue'
import { isEmbedded } from '@/shopify/embedded'
import { isWpEmbedded } from '@/wordpress/embed'
import { useNavCounts } from '@/composables/useNavCounts'

const embedded = isEmbedded()
const inWpAdmin = isWpEmbedded()
const counts = useNavCounts()

const searchOpen = ref(false)

// Ctrl+K / Cmd+K opens the command palette from anywhere.
function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>
