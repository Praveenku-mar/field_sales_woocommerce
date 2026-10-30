<template>
  <!-- Shopify App Pricing plan (OAuth installs only; renders nothing otherwise). -->
  <SectionCard v-if="info.data?.oauth" title="App plan">
    <template #actions>
      <Button
        v-if="info.data.pricing_url"
        label="Change plan"
        icon-left="lucide-external-link"
        @click="openPricing"
      />
    </template>
    <p v-if="!info.data.known" class="text-base text-ink-gray-5">
      The plan can't be read (Partner API not configured), so no plan limits apply.
    </p>
    <dl v-else class="grid grid-cols-1 gap-3 text-base md:grid-cols-3">
      <div>
        <dt class="text-sm text-ink-gray-5">Plan</dt>
        <dd class="text-ink-gray-9">{{ info.data.plan || 'No active plan' }}</dd>
      </div>
      <div v-for="(label, kind) in KINDS" :key="kind">
        <dt class="text-sm text-ink-gray-5">{{ label }}</dt>
        <dd class="text-ink-gray-9">
          {{ info.data.usage[kind] }}
          <span v-if="info.data.limits[kind] != null"> of {{ info.data.limits[kind] }}</span>
          <span v-else class="text-ink-gray-5"> (no limit)</span>
        </dd>
      </div>
    </dl>
  </SectionCard>
</template>

<script setup>
import { Button, createResource } from 'frappe-ui'
import SectionCard from '@/components/SectionCard.vue'

const KINDS = { sales_reps: 'Active sales reps', orders_per_month: 'Orders this month' }

const info = createResource({ url: 'field_sales_woocommerce.shopify.billing.get_plan_info', method: 'GET', auto: true })

// Shopify's plan page must open in the admin's own window, not a pop-up (App Store rule).
function openPricing() {
  window.open(info.data.pricing_url, '_top')
}
</script>
