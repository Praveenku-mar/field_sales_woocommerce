<template>
  <div v-if="doc.get.loading && !doc.doc" class="fs-card flex justify-center py-16 text-ink-gray-5">
    <LoadingIndicator class="size-5" />
  </div>

  <div v-else-if="doc.get.error || !doc.doc" class="fs-card">
    <EmptyState icon="lucide-circle-alert" title="Could not load invoice settings" :description="errorText" />
  </div>

  <div v-else class="fs-card">
    <!-- Order confirmation -->
    <section class="p-8">
      <h3 class="text-base font-medium text-ink-gray-9">Order Confirmation</h3>
      <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
        <Checkbox
          v-model="d.send_order_confirmation"
          label="Send order confirmation"
          description="Email the customer a tax invoice (PDF) once the store confirms the order. Needs the customer's email and an outgoing email account."
        />
      </div>
    </section>

    <!-- Seller details printed on the tax invoice -->
    <section class="border-t border-desk-border p-4">
      <h3 class="text-base font-medium text-ink-gray-9">Company Details</h3>
      <p class="mt-1 text-sm text-ink-gray-5">Printed as the seller on the tax invoice.</p>
      <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
        <TextInput v-model="d.company_name" label="Company Name" />
        <TextInput v-model="d.company_gstin" label="GSTIN" />
        <Textarea v-model="d.company_address" label="Address" :rows="3" />
        <TextInput
          v-model="d.company_state"
          label="State"
          description="Same state as the customer: CGST + SGST. Otherwise: IGST."
        />
        <TextInput v-model="d.company_phone" label="Phone" />
        <TextInput v-model="d.company_email" label="Email" type="email" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Checkbox, LoadingIndicator, Textarea, TextInput, createDocumentResource, toast } from 'frappe-ui'
import { isEmbedded } from '@/shopify/embedded'
import { createMerchantStoreDoc } from '@/shopify/merchantSettings'
import EmptyState from '@/components/EmptyState.vue'

const emit = defineEmits(['saved'])

// Store Connection is a Single; its name is the doctype itself.
// Inside the Shopify admin: the merchant endpoints (managers may not edit Store Connection directly).
const doc = isEmbedded() ? createMerchantStoreDoc() : createDocumentResource({ doctype: 'Store Connection', name: 'Store Connection' })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

const saving = ref(false)
async function save() {
  saving.value = true
  try {
    await doc.save.submit()
    toast.success('Invoice settings saved')
    emit('saved')
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not save invoice settings')
  } finally {
    saving.value = false
  }
}

// The Settings page header renders Discard / Save Changes from these.
const isDirty = computed(() => !!doc.isDirty)
function discard() {
  doc.reload()
}
defineExpose({ isDirty, saving, save, discard })
</script>
