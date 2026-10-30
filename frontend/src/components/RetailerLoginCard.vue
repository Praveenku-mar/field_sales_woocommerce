<template>
  <!-- Managers only (the parent gates with session.isManager): invite and manage a
       customer's My Orders login. The APIs check the manager role again on the server. -->
  <SectionCard title="Login Access">
    <template #actions>
      <span v-if="loginData.user" class="flex items-center gap-1.5 text-xs text-ink-gray-5">
        <span class="lucide-user-round-check size-3.5" />
        {{ loginData.enabled ? 'Enabled' : 'Disabled' }}
      </span>
    </template>

    <div v-if="!loginData.user && !login.loading" class="space-y-3">
      <p class="text-sm text-ink-gray-6">
        Give this customer a sign-in to see their own orders in the app.
      </p>
      <TextInput
        v-model="email"
        label="Email"
        type="email"
        required
        :placeholder="fallbackEmail || 'customer@example.com'"
      />
      <FormControl v-model="sendInvite" type="checkbox" label="Email an invite to set a password" />
      <Button
        variant="solid"
        label="Send Invite"
        icon-left="lucide-mail-plus"
        :loading="inviting"
        :disabled="!EMAIL.test(email)"
        @click="invite"
      />
    </div>

    <div v-else-if="loginData.user" class="space-y-1">
      <div class="flex items-center justify-between gap-3">
        <span class="min-w-0 truncate text-ink-gray-8">{{ loginData.user }}</span>
        <span class="shrink-0"><StatusPill :status="loginData.enabled ? 'Active' : 'Inactive'" /></span>
      </div>
      <p class="pt-1 text-xs text-ink-gray-5">Signs in at {{ appLoginUrl }} to see this customer's orders.</p>
      <div class="flex flex-wrap gap-2 pt-3">
        <Button label="Send reset link" size="sm" icon-left="lucide-key-round" :loading="resetting" @click="sendReset" />
        <Button label="Remove access" size="sm" icon-left="lucide-user-round-x" @click="confirmOpen = true" />
      </div>
    </div>
  </SectionCard>

  <Dialog v-model="confirmOpen" title="Remove login access" size="sm">
    <p class="text-p-base text-ink-gray-7">
      {{ loginData.user }} will be signed out everywhere and can no longer log in. The customer's record
      and orders stay untouched.
    </p>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="removing" @click="confirmOpen = false" />
        <Button variant="solid" label="Remove access" :loading="removing" @click="remove" />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Button, Dialog, FormControl, TextInput, createResource, toast } from 'frappe-ui'
import SectionCard from '@/components/SectionCard.vue'
import StatusPill from '@/components/StatusPill.vue'
import { callMethod } from '@/utils/api'
import { APP_BASE } from '@/utils/base'

const props = defineProps({
  /** The Retailer whose login this card manages. */
  retailer: { type: String, required: true },
  /** Retailer.email, as the prefill for the invite. */
  fallbackEmail: { type: String, default: '' },
})
const emit = defineEmits(['updated'])

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const appLoginUrl = `${window.location.origin}${APP_BASE}/login`

const login = createResource({
  url: 'field_sales_woocommerce.api.get_retailer_login',
  params: { retailer: props.retailer },
  auto: true,
})
const loginData = computed(() => login.data || {})

const email = ref(props.fallbackEmail || '')
const sendInvite = ref(true)
const inviting = ref(false)

async function invite() {
  inviting.value = true
  try {
    const result = await callMethod('field_sales_woocommerce.api.create_retailer_login', {
      retailer: props.retailer,
      email: email.value.trim(),
      send_invite: sendInvite.value ? 1 : 0,
    })
    toast.success(result.user_created ? `Invited ${result.user}` : `${result.user} can now sign in`)
    login.reload()
    emit('updated')
  } catch {
    // callMethod already toasted the error (disabled user, staff email, no outgoing email…).
  } finally {
    inviting.value = false
  }
}

const resetting = ref(false)
async function sendReset() {
  resetting.value = true
  try {
    const result = await callMethod('field_sales_woocommerce.api.send_retailer_password_reset', {
      retailer: props.retailer,
    })
    toast.success(`Reset link emailed to ${result.email}`)
  } catch {
    // callMethod already toasted the error.
  } finally {
    resetting.value = false
  }
}

const confirmOpen = ref(false)
const removing = ref(false)
async function remove() {
  removing.value = true
  try {
    await callMethod('field_sales_woocommerce.api.revoke_retailer_login', { retailer: props.retailer })
    toast.success('Login removed')
    confirmOpen.value = false
    login.reload()
    emit('updated')
  } catch {
    // callMethod already toasted the error.
  } finally {
    removing.value = false
  }
}
</script>
