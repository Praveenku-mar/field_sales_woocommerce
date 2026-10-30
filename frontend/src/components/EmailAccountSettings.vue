<template>
  <div class="fs-card overflow-hidden">
    <header class="flex flex-wrap items-center justify-between gap-2 border-b border-desk-border px-4 py-3">
      <div>
        <h3 class="text-base font-medium text-ink-gray-9">Outgoing Email</h3>
        <p class="text-sm text-ink-gray-5">Sends rep invites, password resets and the daily manager summary.</p>
      </div>
      <Button variant="solid" label="New Email Account" icon-left="lucide-plus" @click="openForm()" />
    </header>

    <ErrorMessage v-if="accounts.list.error" class="px-4 pt-3" :message="accounts.list.error" />

    <div v-if="accounts.list.loading && !accounts.data" class="flex justify-center py-12 text-ink-gray-5">
      <LoadingIndicator class="size-5" />
    </div>
    <ul v-else-if="accounts.data?.length" class="divide-y divide-desk-border">
      <li v-for="a in accounts.data" :key="a.name">
        <button
          type="button"
          class="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-surface-gray-1 focus-visible:bg-surface-gray-1 focus-visible:outline-none"
          @click="openForm(a)"
        >
          <span class="lucide-mail size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
          <span class="min-w-0 flex-1">
            <span class="block truncate font-medium text-ink-gray-9">{{ a.email_id }}</span>
            <span class="block truncate text-xs text-ink-gray-5">
              {{ [a.name !== a.email_id ? a.name : null, a.service || 'Custom', a.smtp_server].filter(Boolean).join(' · ') }}
            </span>
          </span>
          <Badge v-if="a.default_outgoing" theme="blue" label="Default" />
          <Badge :theme="a.enable_outgoing ? 'green' : 'gray'" :label="a.enable_outgoing ? 'Enabled' : 'Disabled'" />
          <span class="lucide-chevron-right size-4 shrink-0 text-ink-gray-4" aria-hidden="true" />
        </button>
      </li>
    </ul>
    <EmptyState
      v-else
      icon="lucide-mail"
      title="No email account"
      description="Add one so the app can send invites and summaries. Without it, emails stay in the queue."
    />

    <Dialog v-model="form.open" :title="form.name ? 'Edit Email Account' : 'New Email Account'" size="xl">
      <form id="email-account" class="space-y-6" @submit.prevent="save">
        <section class="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
          <FormControl
            v-model="f.service"
            type="select"
            label="Service"
            :options="SERVICES"
            description="Fills in the server settings for common providers"
            @update:model-value="applyPreset"
          />
          <FormControl v-model="f.email_id" label="Email Address" type="email" required :disabled="!!form.name" autocomplete="off" />
          <FormControl
            v-if="!form.name"
            v-model="f.email_account_name"
            label="Account Name"
            placeholder="Defaults to the email address"
          />
          <FormControl
            v-model="f.password"
            label="Password"
            :type="form.showPassword ? 'text' : 'password'"
            autocomplete="new-password"
            :required="!form.name"
            :placeholder="form.name ? 'Leave blank to keep the saved password' : ''"
            :description="passwordHint"
          >
            <template #suffix>
              <button
                type="button"
                class="flex text-ink-gray-5 hover:text-ink-gray-8"
                :aria-label="form.showPassword ? 'Hide password' : 'Show password'"
                @click="form.showPassword = !form.showPassword"
              >
                <span :class="form.showPassword ? 'lucide-eye-off' : 'lucide-eye'" class="size-4" />
              </button>
            </template>
          </FormControl>
          <div class="space-y-3 md:col-span-2">
            <Checkbox v-model="f.login_id_is_different" label="Sign in with a different login ID" />
            <FormControl v-if="f.login_id_is_different" v-model="f.login_id" label="Login ID" required class="md:w-1/2 md:pr-4" />
          </div>
        </section>

        <section class="border-t border-desk-border pt-5">
          <h4 class="text-sm font-medium text-ink-gray-9">SMTP Server</h4>
          <div class="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
            <FormControl v-model="f.smtp_server" label="Server" placeholder="smtp.example.com" required />
            <FormControl v-model="f.smtp_port" label="Port" type="number" placeholder="587" />
            <FormControl
              v-model="f.security"
              type="select"
              label="Security"
              :options="SECURITY"
              @update:model-value="(value) => movePort(value)"
            />
          </div>
        </section>

        <section class="space-y-3 border-t border-desk-border pt-5">
          <Checkbox v-if="form.name" v-model="f.enable_outgoing" label="Enabled" />
          <Checkbox v-model="f.default_outgoing" label="Default outgoing account" description="Used for emails that do not name an account" />
          <Checkbox v-model="f.always_use_account_email_id_as_sender" label="Always send from this address" />
        </section>

        <p class="text-xs text-ink-gray-5">Saving connects to the server to check these settings, which can take a few seconds.</p>
      </form>

      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" :disabled="form.saving" @click="form.open = false" />
          <Button type="submit" form="email-account" variant="solid" :label="form.name ? 'Save' : 'Create'" :loading="form.saving" :disabled="!canSave" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  ErrorMessage,
  FormControl,
  LoadingIndicator,
  createListResource,
  toast,
} from 'frappe-ui'
import { isEmbedded } from '@/shopify/embedded'
import { createMerchantEmailAccounts, saveMerchantEmailAccountUrl } from '@/shopify/merchantSettings'
import EmptyState from './EmptyState.vue'
import { callMethod } from '@/utils/api'

// Outgoing (SMTP, password sign-in) accounts only. Incoming mail, OAuth and Frappe Mail need
// more settings than this app uses: configure those in the desk's Email Account form.
const SERVICES = [
  { label: 'Custom', value: 'Custom' },
  ...['GMail', 'Outlook.com', 'Sendgrid', 'SparkPost', 'Yahoo Mail', 'Yandex.Mail'].map((s) => ({ label: s, value: s })),
]
// Same SMTP values as the desk form's frappe.email_defaults.
const SMTP_PRESETS = {
  GMail: 'smtp.gmail.com',
  'Outlook.com': 'smtp-mail.outlook.com',
  Sendgrid: 'smtp.sendgrid.net',
  SparkPost: 'smtp.sparkpostmail.com',
  'Yahoo Mail': 'smtp.mail.yahoo.com',
  'Yandex.Mail': 'smtp.yandex.com',
}
const SECURITY = [
  { label: 'STARTTLS (port 587)', value: 'tls' },
  { label: 'SSL/TLS (port 465)', value: 'ssl' },
  { label: 'None', value: 'none' },
]
const FIELDS = [
  'name',
  'email_id',
  'service',
  'smtp_server',
  'smtp_port',
  'use_tls',
  'use_ssl_for_outgoing',
  'login_id_is_different',
  'login_id',
  'enable_outgoing',
  'default_outgoing',
  'always_use_account_email_id_as_sender',
]

// Inside the Shopify admin the manager can't read Email Account directly: merchant endpoints.
const embedded = isEmbedded()
const accounts = embedded
  ? createMerchantEmailAccounts()
  : createListResource({
      doctype: 'Email Account',
      fields: FIELDS,
      orderBy: 'default_outgoing desc, modified desc',
      pageLength: 50,
      auto: true,
    })

const form = reactive({ open: false, name: null, saving: false, showPassword: false })
const blank = () => ({
  service: 'Custom',
  email_id: '',
  email_account_name: '',
  password: '',
  login_id_is_different: false,
  login_id: '',
  smtp_server: '',
  smtp_port: '587',
  security: 'tls',
  enable_outgoing: true,
  default_outgoing: false,
  always_use_account_email_id_as_sender: false,
})
const f = reactive(blank())

function openForm(account = null) {
  Object.assign(f, blank())
  form.name = account?.name || null
  form.showPassword = false
  if (account) {
    Object.assign(f, {
      service: account.service || 'Custom',
      email_id: account.email_id,
      smtp_server: account.smtp_server || '',
      smtp_port: account.smtp_port || '',
      security: account.use_ssl_for_outgoing ? 'ssl' : account.use_tls ? 'tls' : 'none',
      login_id_is_different: !!account.login_id_is_different,
      login_id: account.login_id || '',
      enable_outgoing: !!account.enable_outgoing,
      default_outgoing: !!account.default_outgoing,
      always_use_account_email_id_as_sender: !!account.always_use_account_email_id_as_sender,
    })
  }
  lastSecurity = f.security
  form.open = true
}

// Change handlers, not watchers: loading an account into the form must not rewrite it.
// Picking a provider fills in its server.
function applyPreset(service) {
  if (!SMTP_PRESETS[service]) return
  Object.assign(f, { smtp_server: SMTP_PRESETS[service], smtp_port: '587', security: 'tls' })
  lastSecurity = 'tls'
}
// The port follows the security choice while it is still a standard one.
const STANDARD_PORTS = { tls: '587', ssl: '465', none: '25' }
let lastSecurity = 'tls'
function movePort(security) {
  if (String(f.smtp_port) === STANDARD_PORTS[lastSecurity]) f.smtp_port = STANDARD_PORTS[security]
  lastSecurity = security
}

const passwordHint = computed(() => {
  if (f.service === 'GMail') return 'Use a Google app password, not your Gmail password'
  if (f.service === 'Sendgrid') return 'Your SendGrid API key (login ID is set to "apikey")'
  return null
})

const canSave = computed(
  () =>
    !!f.email_id.trim() &&
    !!f.smtp_server.trim() &&
    (form.name || !!f.password) &&
    (!f.login_id_is_different || !!f.login_id.trim()),
)

async function save() {
  if (!canSave.value) return
  form.saving = true
  const values = {
    service: f.service === 'Custom' ? null : f.service,
    smtp_server: f.smtp_server.trim(),
    smtp_port: f.smtp_port ? String(f.smtp_port) : null,
    use_tls: f.security === 'tls' ? 1 : 0,
    use_ssl_for_outgoing: f.security === 'ssl' ? 1 : 0,
    login_id_is_different: f.login_id_is_different ? 1 : 0,
    login_id: f.login_id_is_different ? f.login_id.trim() : null,
    enable_outgoing: f.enable_outgoing ? 1 : 0,
    default_outgoing: f.default_outgoing ? 1 : 0,
    always_use_account_email_id_as_sender: f.always_use_account_email_id_as_sender ? 1 : 0,
  }
  if (f.password) values.password = f.password
  try {
    if (embedded) {
      const email = { ...values, email_id: f.email_id.trim(), email_account_name: f.email_account_name.trim() || null }
      const saved = await callMethod(saveMerchantEmailAccountUrl, { name: form.name, values: email })
      toast.success(form.name ? 'Email account saved' : `Email account ${saved.name} created`)
    } else if (form.name) {
      await callMethod('frappe.client.set_value', { doctype: 'Email Account', name: form.name, fieldname: values })
      toast.success('Email account saved')
    } else {
      const doc = await callMethod('frappe.client.insert', {
        doc: {
          doctype: 'Email Account',
          email_id: f.email_id.trim(),
          email_account_name: f.email_account_name.trim() || null,
          auth_method: 'Basic',
          enable_incoming: 0,
          ...values,
          enable_outgoing: 1,
        },
      })
      toast.success(`Email account ${doc.name} created`)
    }
    form.open = false
    accounts.reload()
  } catch {
    // callMethod already toasted the server's message (e.g. the SMTP login failed). The form is kept.
  } finally {
    form.saving = false
  }
}
</script>
