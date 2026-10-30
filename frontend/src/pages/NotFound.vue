<template>
  <div class="px-5 pt-5">
    <div class="fs-card">
      <EmptyState icon="lucide-compass" title="Page not found" description="This page does not exist.">
        <Button label="Go to Dashboard" :route="{ name: 'Dashboard' }" />
      </EmptyState>
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
</script>
