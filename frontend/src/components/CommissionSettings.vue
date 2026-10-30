<template>
  <div v-if="doc.get.loading && !doc.doc" class="fs-card flex justify-center py-16 text-ink-gray-5">
    <LoadingIndicator class="size-5" />
  </div>

  <div v-else-if="doc.get.error || !doc.doc" class="fs-card">
    <EmptyState icon="lucide-circle-alert" title="Could not load commission settings" :description="errorText" />
  </div>

  <div v-else class="fs-card">
    <section class="p-8">
      <h3 class="text-base font-medium text-ink-gray-9">Commission</h3>
      <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
        <Checkbox
          v-model="d.enable_commission"
          label="Pay commission to sales reps"
          description="Turn off if this company does not pay commission. New orders then create no commission entries, no monthly statements are made, and the Commission screens are hidden. Existing records are kept."
        />
      </div>
    </section>
  </div>

  <Dialog
    v-model="confirmOff"
    title="Turn off commission?"
    message="Sales reps will stop earning commission on new orders, no monthly statements will be made, and the Commission screens will be hidden. Existing commission records and statements are kept. You can turn it back on at any time."
    icon="lucide-triangle-alert"
    theme="red"
  >
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="saving" @click="confirmOff = false" />
        <Button variant="solid" theme="red" label="Turn Off Commission" :loading="saving" @click="turnOff" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Checkbox, Dialog, LoadingIndicator, createDocumentResource, toast } from 'frappe-ui'
import { isEmbedded } from '@/shopify/embedded'
import { createMerchantStoreDoc } from '@/shopify/merchantSettings'
import EmptyState from '@/components/EmptyState.vue'
import { useSessionStore } from '@/stores/session'

const emit = defineEmits(['saved'])
const session = useSessionStore()

// Store Connection is a Single; its name is the doctype itself.
// Inside the Shopify admin: the merchant endpoints (managers may not edit Store Connection directly).
const doc = isEmbedded() ? createMerchantStoreDoc() : createDocumentResource({ doctype: 'Store Connection', name: 'Store Connection' })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

const saving = ref(false)
const confirmOff = ref(false)

// Turning commission off asks first; turning it on (or saving with no change to it) does not.
function save() {
  if (doc.originalDoc?.enable_commission && !d.value.enable_commission) {
    confirmOff.value = true
    return
  }
  return persist()
}

async function turnOff() {
  await persist()
  confirmOff.value = false
}

async function persist() {
  saving.value = true
  try {
    await doc.save.submit()
    // The sidebar and routes read the setting from the session.
    await session.reload()
    toast.success(d.value.enable_commission ? 'Commission is on' : 'Commission is off')
    emit('saved')
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not save commission settings')
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
