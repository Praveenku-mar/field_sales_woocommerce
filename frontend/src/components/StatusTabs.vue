<template>
  <!-- WooCommerce → Status: the sync log and the records that failed to reach the store. -->
  <WpNavTabs label="Status" :tabs="TABS" />
</template>

<script setup>
import WpNavTabs from './wp/WpNavTabs.vue'

const TABS = [
  { label: 'Logs', route: { name: 'SyncLogs' } },
  { label: 'Failures', route: { name: 'SyncFailures' } },
]
</script>
