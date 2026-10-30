<template>
  <div v-if="doc.get.loading && !doc.doc" class="fs-card flex justify-center py-16 text-ink-gray-5">
    <LoadingIndicator class="size-5" />
  </div>

  <div v-else-if="doc.get.error || !doc.doc" class="fs-card">
    <EmptyState icon="lucide-circle-alert" title="Could not load Razorpay settings" :description="errorText" />
  </div>

  <div v-else class="space-y-4">
    <!-- Outcome of Test Connection on this visit -->
    <div
      v-if="testResult"
      role="status"
      class="flex items-start gap-2.5 rounded-3 border px-4 py-3 text-sm"
      :class="testResult.ok ? 'border-outline-green-2 bg-surface-green-1 text-ink-green-7' : 'border-outline-red-2 bg-surface-red-1 text-ink-red-6'"
    >
      <span :class="[testResult.ok ? 'lucide-circle-check' : 'lucide-circle-x', 'mt-0.5 size-4 shrink-0']" aria-hidden="true" />
      <p class="min-w-0 flex-1 break-words">
        <span class="font-semibold">{{ testResult.ok ? 'Connection successful.' : 'Connection failed.' }}</span>
        {{ testResult.message }}
      </p>
      <button
        type="button"
        class="flex shrink-0 rounded-3 p-0.5 opacity-70 hover:opacity-100"
        aria-label="Dismiss"
        @click="testResult = null"
      >
        <span class="lucide-x size-4" />
      </button>
    </div>

    <!-- The App Store edition: links are off; the store's own checkout pays (Shopify rule). -->
    <div
      v-if="locked"
      role="note"
      class="flex items-start gap-2.5 rounded-3 border border-outline-blue-2 bg-surface-blue-1 px-4 py-3 text-sm text-ink-blue-7"
    >
      <span class="lucide-circle-info mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <p class="min-w-0 flex-1 break-words">
        <span class="font-semibold">Payment links are off in the Shopify App Store edition.</span>
        Shopify requires payment for store orders to happen in the store's checkout. To accept
        Razorpay, install Shopify's official Razorpay app on your store: retailers then pay with the
        order's <span class="font-medium">Pay in Store Checkout</span> link.
      </p>
    </div>

    <div class="fs-card">
      <!-- Connection -->
      <section class="p-8">
        <h3 class="text-base font-medium text-ink-gray-9">Connection</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <Checkbox
            v-model="d.enabled"
            label="Enabled"
            description="Let reps create Razorpay payment links for submitted orders"
            :disabled="locked"
          />
          <Select
            v-model="d.mode"
            :options="MODES"
            label="Mode"
            description="For display only: Razorpay decides the mode from the key (rzp_test_ / rzp_live_)"
            :disabled="locked"
          />
        </div>
      </section>

      <!-- API keys -->
      <section class="border-t border-desk-border p-4">
        <div class="flex items-center justify-between gap-2">
          <h3 class="text-base font-medium text-ink-gray-9">API Keys</h3>
          <span class="inline-flex items-center gap-1.5 text-xs text-ink-gray-5">
            <span class="lucide-lock size-3.5" />
            Stored encrypted
          </span>
        </div>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput
            v-model="d.key_id"
            label="Key ID"
            placeholder="rzp_test_…"
            :required="!!d.enabled"
            :description="keyModeHint"
            :disabled="locked"
          />
          <TextInput
            v-model="d.key_secret"
            :type="secretsShown.key_secret ? 'text' : 'password'"
            label="Key Secret"
            :required="!!d.enabled"
            description="Stored encrypted — the masked value is kept unless you type a new secret"
            :disabled="locked"
          >
            <template #suffix><SecretToggle field="key_secret" /></template>
          </TextInput>
        </div>
        <div class="mt-6">
          <Button variant="outline" label="Test Connection" icon-left="lucide-cable" :loading="testing" :disabled="locked" @click="testConnection" />
        </div>
      </section>

      <!-- Webhook -->
      <section class="border-t border-desk-border p-4">
        <h3 class="text-base font-medium text-ink-gray-9">Webhook</h3>
        <p class="mt-1 text-sm text-ink-gray-5">
          In the Razorpay Dashboard, add a webhook with this URL for the <span class="font-mono">payment_link.paid</span> event, then enter the secret you chose there.
        </p>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput :model-value="webhookUrl.data || d.webhook_url || ''" label="Webhook URL" readonly>
            <template #suffix>
              <button
                type="button"
                class="flex size-5 items-center justify-center rounded text-ink-gray-5 hover:text-ink-gray-8"
                aria-label="Copy Webhook URL"
                title="Copy"
                @click="copyWebhookUrl"
              >
                <span class="lucide-copy size-3.5" />
              </button>
            </template>
          </TextInput>
          <TextInput
            v-model="d.webhook_secret"
            :type="secretsShown.webhook_secret ? 'text' : 'password'"
            label="Webhook Secret"
            description="The secret entered on the Razorpay webhook (not the key secret)"
            :disabled="locked"
          >
            <template #suffix><SecretToggle field="webhook_secret" /></template>
          </TextInput>
        </div>
      </section>

      <!-- Payment links -->
      <section class="border-t border-desk-border p-4">
        <h3 class="text-base font-medium text-ink-gray-9">Payment Links</h3>
        <div class="mt-6 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
          <TextInput
            v-model.number="d.link_expiry_hours"
            type="number"
            min="0"
            label="Link Expiry (Hours)"
            description="0 = the link never expires"
            :disabled="locked"
          />
          <TextInput
            v-model="d.callback_url"
            type="url"
            label="Callback URL"
            placeholder="https://…"
            description="Optional: where the retailer is sent after paying"
            :disabled="locked"
          />
          <Checkbox v-model="d.notify_sms" label="Send link by SMS" description="Razorpay texts the link to the retailer's phone" :disabled="locked" />
          <Checkbox v-model="d.notify_email" label="Send link by email" description="Razorpay emails the link to the retailer" :disabled="locked" />
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, reactive, ref } from 'vue'
import { Button, Checkbox, LoadingIndicator, Select, TextInput, createDocumentResource, createResource, toast } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { callMethod } from '@/utils/api'

// Razorpay Settings is a Single; its name is the doctype itself.
const doc = createDocumentResource({ doctype: 'Razorpay Settings', name: 'Razorpay Settings' })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

// frappe.client.get skips the doctype's onload, so the URL comes from its own call.
const webhookUrl = createResource({ url: 'field_sales_woocommerce.payments.razorpay.get_webhook_url', method: 'GET', auto: true })

// Payment links are off on the Shopify App Store edition; the settings stay readable.
const config = createResource({ url: 'field_sales_woocommerce.payments.razorpay.get_payment_config', method: 'GET', auto: true })
const locked = computed(() => config.data?.available === false)

const MODES = ['Test', 'Live'].map((v) => ({ label: v, value: v }))

// Mode is a label only; point out when it disagrees with the key.
const keyModeHint = computed(() => {
  const key = (d.value.key_id || '').trim()
  const keyMode = key.startsWith('rzp_live_') ? 'Live' : key.startsWith('rzp_test_') ? 'Test' : null
  if (keyMode && d.value.mode && keyMode !== d.value.mode) return `This is a ${keyMode} key, but Mode says ${d.value.mode}`
  return 'Razorpay Dashboard → Account & Settings → API Keys'
})

// Eye toggle rendered inside each secret input's suffix.
const secretsShown = reactive({})
const SecretToggle = defineComponent({
  props: { field: { type: String, required: true } },
  setup(props) {
    return () =>
      h(
        'button',
        {
          type: 'button',
          class: 'flex size-5 items-center justify-center rounded text-ink-gray-5 hover:text-ink-gray-8',
          'aria-label': secretsShown[props.field] ? `Hide ${props.field}` : `Show ${props.field}`,
          title: secretsShown[props.field] ? 'Hide' : 'Show',
          onClick: () => (secretsShown[props.field] = !secretsShown[props.field]),
        },
        h('span', { class: secretsShown[props.field] ? 'lucide-eye-off size-3.5' : 'lucide-eye size-3.5' }),
      )
  },
})

async function copyWebhookUrl() {
  try {
    await navigator.clipboard.writeText(webhookUrl.data || d.value.webhook_url || '')
    toast.success('Webhook URL copied')
  } catch {
    toast.error('Could not copy to clipboard')
  }
}

const saving = ref(false)
async function save() {
  saving.value = true
  let saved = false
  try {
    await doc.save.submit()
    saved = true
    toast.success('Razorpay settings saved')
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not save Razorpay settings')
  } finally {
    saving.value = false
  }
  return saved
}

const testResult = ref(null)
const testing = ref(false)
async function testConnection() {
  testing.value = true
  try {
    // The server tests the saved keys, so unsaved edits are saved first.
    if (doc.isDirty && !(await save())) return
    testResult.value = await callMethod('field_sales_woocommerce.payments.razorpay.test_connection')
  } catch {
    // callMethod already toasted the error message.
  } finally {
    testing.value = false
  }
}

// The Settings page header renders Discard / Save Changes from these.
const isDirty = computed(() => !!doc.isDirty)
function discard() {
  doc.reload()
}
defineExpose({ isDirty, saving, testing, save, discard })
</script>
