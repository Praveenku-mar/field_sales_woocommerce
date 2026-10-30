<template>
  <!-- Guests land here (the page gate redirects them); staff sign in on the same form. -->
  <div class="flex min-h-screen items-center justify-center bg-desk-bg p-5">
    <div class="fs-card w-full max-w-sm p-6">
      <div class="flex flex-col items-center gap-2 text-center">
        <!-- Runtime URL (Frappe serves it from the app's public dir), not a bundled asset. -->
        <img :src="LOGO" alt="" class="h-9 w-9" />
        <h1 class="text-xl font-semibold text-ink-gray-9">Field Sales</h1>
        <p class="text-sm text-ink-gray-6">Sign in to continue</p>
      </div>

      <!-- A session already exists (e.g. an expired-app bounce): offer to keep it. -->
      <div
        v-if="signedInAs"
        class="mt-5 flex items-center gap-2 rounded-4 bg-surface-gray-1 px-3 py-2.5 text-sm text-ink-gray-7"
      >
        <span class="lucide-user-round size-4 shrink-0 text-ink-gray-5" />
        <span class="min-w-0 flex-1 truncate">Signed in as {{ signedInAs }}</span>
        <a :href="continueHref" class="shrink-0 text-sm text-ink-blue-6 hover:underline">Continue</a>
      </div>

      <form class="mt-5 space-y-4" @submit.prevent="submit">
        <TextInput
          v-model="email"
          label="Email"
          type="email"
          required
          placeholder="you@example.com"
          autocomplete="username"
        />
        <TextInput
          v-model="password"
          label="Password"
          type="password"
          required
          autocomplete="current-password"
        />
        <p
          v-if="error"
          class="flex items-start gap-2 rounded-4 bg-surface-red-1 px-3 py-2.5 text-sm text-ink-red-5"
          aria-live="polite"
        >
          <span class="lucide-circle-alert mt-px size-4 shrink-0" />{{ error }}
        </p>
        <Button type="submit" class="w-full" variant="solid" label="Log in" :loading="submitting" />
        <button
          type="button"
          class="block w-full text-center text-sm text-ink-gray-6 hover:text-ink-gray-8 hover:underline"
          @click="forgotOpen = true"
        >
          Forgot password?
        </button>
      </form>
    </div>
  </div>

  <Dialog v-model="forgotOpen" title="Reset your password" size="sm">
    <div class="space-y-4">
      <p class="text-p-base text-ink-gray-7">
        Enter your email and we will send you a link to set a new password.
      </p>
      <TextInput v-model="forgotEmail" label="Email" type="email" required placeholder="you@example.com" />
    </div>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button label="Cancel" :disabled="sendingReset" @click="forgotOpen = false" />
        <Button
          variant="solid"
          label="Email me a reset link"
          :loading="sendingReset"
          :disabled="!EMAIL.test(forgotEmail)"
          @click="sendReset"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Button, Dialog, TextInput, createResource, toast } from 'frappe-ui'
import { cookieUser } from '@/stores/session'
import { callMethod } from '@/utils/api'
import { APP_BASE } from '@/utils/base'

const route = useRoute()
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const LOGO = '/assets/field_sales/images/logo.svg'

const signedInAs = cookieUser()
const continueHref = window.default_route || APP_BASE

const email = ref('')
const password = ref('')
const error = ref(null)
const submitting = ref(false)

/** Only same-site paths: ?redirect-to=/fieldsales/orders/FO-…, never an external URL. */
const redirectTarget = computed(() => {
  const target = route.query['redirect-to']
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
    ? target
    : continueHref
})

const login = createResource({ url: 'login' })

async function submit() {
  submitting.value = true
  error.value = null
  try {
    await login.submit({ usr: email.value.trim(), pwd: password.value })
    // A full load: fresh boot values and a session the next page can rely on.
    window.location.href = redirectTarget.value
  } catch (e) {
    error.value =
      e?.messages?.[0] || e?.message?.replace(/<[^>]*>/g, '') || 'Incorrect email or password'
  } finally {
    submitting.value = false
  }
}

const forgotOpen = ref(false)
const forgotEmail = ref('')
const sendingReset = ref(false)

async function sendReset() {
  sendingReset.value = true
  try {
    // Frappe's own guest flow: emails a one-time set-password link.
    await callMethod('frappe.core.doctype.user.user.reset_password', { user: forgotEmail.value.trim() })
    toast.success('If that email has an account, a reset link is on its way')
    forgotOpen.value = false
  } catch {
    // callMethod already toasted the error.
  } finally {
    sendingReset.value = false
  }
}
</script>
