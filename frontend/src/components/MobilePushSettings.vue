<template>
  <div v-if="doc.get.loading && !doc.doc" class="fs-card flex justify-center py-16 text-ink-gray-5">
    <LoadingIndicator class="size-5" />
  </div>
  <div v-else-if="doc.get.error || !doc.doc" class="fs-card">
    <EmptyState icon="lucide-circle-alert" title="Could not load notification settings" :description="errorText" />
  </div>
  <div v-else class="space-y-4">
    <div class="fs-card">
      <section class="p-6">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h3 class="text-base font-medium text-ink-gray-9">Notification</h3>
          <Button
            v-if="d.enabled"
            variant="outline"
            label="Send Test Notification"
            icon-left="lucide-send"
            :disabled="!status.data?.configured || !!doc.isDirty || saving"
            @click="openTestDialog"
          />
        </div>
        <p class="mt-1 text-sm text-ink-gray-5">Send app notifications to registered Android and iOS phones.</p>
        <div class="mt-5 grid gap-5 md:grid-cols-2">
          <Checkbox
            v-model="d.enabled"
            label="Enable this site's Firebase key"
            description="A server-wide key may still provide push when this is off."
          />
          <div class="text-sm">
            <p class="font-medium text-ink-gray-9">Current connection</p>
            <p class="mt-1 text-ink-gray-6">{{ connectionLabel }}</p>
            <p class="mt-1 text-ink-gray-5">{{ status.data?.registered_phones ?? '—' }} enabled phone(s) registered</p>
          </div>
        </div>
      </section>

      <section v-if="d.enabled" class="border-t border-desk-border p-6">
        <h3 class="text-base font-medium text-ink-gray-9">Firebase service account</h3>
        <p class="mt-1 text-sm text-ink-gray-5">
          Upload the service-account JSON from Firebase Project settings → Service accounts.
          The server stores it encrypted and removes the uploaded copy. Uploading saves this page's current changes.
          Android's google-services.json belongs in the app build.
        </p>
        <p v-if="d.firebase_project_id" class="mt-3 text-sm text-ink-gray-7">Site key project: {{ d.firebase_project_id }}</p>
        <FileUploader file-types=".json" :private="true" @success="saveUploadedKey">
          <template #default="{ openFileSelector, uploading, progress, error }">
            <div class="mt-4 space-y-2">
              <Button
                variant="outline"
                icon-left="lucide-upload"
                :label="uploading ? `Uploading ${progress}%` : 'Upload service-account JSON'"
                :loading="uploading || savingKey"
                :disabled="savingKey"
                @click="openFileSelector"
              />
              <p v-if="error" role="alert" class="text-sm text-ink-red-6">{{ error }}</p>
            </div>
          </template>
        </FileUploader>
      </section>

    </div>

    <Dialog v-model="testDialogOpen" title="Send Test Notification">
      <div class="space-y-3">
        <p class="text-sm text-ink-gray-5">Choose a user signed in to the mobile app.</p>
        <FormControl v-model="selectedUser" type="select" label="Send to" :options="userOptions" :disabled="users.loading || sending" />
        <p v-if="users.loading" role="status" class="text-sm text-ink-gray-5">Loading registered phones…</p>
        <p v-else-if="users.error" role="alert" class="text-sm text-ink-red-6">Could not load registered phones.</p>
        <p v-else-if="!userOptions.length" class="text-sm text-ink-gray-5">No enabled phone is registered yet.</p>
        <p v-if="testResult" role="status" class="text-sm text-ink-gray-7">
          Sent: {{ testResult.sent }} · Failed: {{ testResult.failed }} · Invalid tokens removed: {{ testResult.disabled }}
        </p>
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" :disabled="sending" @click="testDialogOpen = false" />
          <Button variant="solid" label="Send" :loading="sending" :disabled="!selectedUser || users.loading" @click="sendTest" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Button, Checkbox, Dialog, FileUploader, FormControl, LoadingIndicator, createDocumentResource, createResource, toast } from 'frappe-ui'
import EmptyState from '@/components/EmptyState.vue'
import { callMethod } from '@/utils/api'

const METHOD = 'field_sales_woocommerce.field_sales_woocommerce.doctype.mobile_push_settings.mobile_push_settings'
const doc = createDocumentResource({ doctype: 'Mobile Push Settings', name: 'Mobile Push Settings' })
const status = createResource({ url: `${METHOD}.get_push_status`, method: 'GET', auto: true })
const users = createResource({ url: `${METHOD}.users_with_phones`, method: 'GET' })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))
const connectionLabel = computed(() => {
  const config = status.data
  if (!config) return 'Checking Firebase configuration…'
  if (!config.configured) return 'Push is not configured'
  return `${config.source === 'site' ? 'Site key' : 'Server-wide key'} · ${config.project_id}`
})
const userOptions = computed(() => (users.data || []).map((row) => ({
  label: `${row.full_name} (${row.user}) · ${row.phones} phone(s)`,
  value: row.user,
})))
const selectedUser = ref('')
watch(userOptions, (options) => {
  if (!options.some((option) => option.value === selectedUser.value)) selectedUser.value = options[0]?.value || ''
})
const saving = ref(false)
const savingKey = ref(false)
const sending = ref(false)
const testDialogOpen = ref(false)
const testResult = ref(null)

function openTestDialog() {
  testResult.value = null
  testDialogOpen.value = true
  users.reload().catch(() => {})
}

async function save() {
  saving.value = true
  try {
    await doc.save.submit()
    await status.reload()
    toast.success('Notification settings saved')
    return true
  } catch (error) {
    toast.error(error?.messages?.[0] || error?.message || 'Could not save notification settings')
    return false
  } finally {
    saving.value = false
  }
}

async function saveUploadedKey(file) {
  savingKey.value = true
  d.value.service_account_file = file.file_url
  try {
    // Save immediately so the private upload is consumed and deleted by the DocType.
    await save()
  } finally {
    savingKey.value = false
    await doc.reload()
  }
}

async function sendTest() {
  sending.value = true
  testResult.value = null
  try {
    testResult.value = await callMethod(`${METHOD}.send_test_notification`, { user: selectedUser.value })
    if (testResult.value?.sent) toast.success('Test notification sent')
  } catch {
    // callMethod displays the server error.
  } finally {
    sending.value = false
    await Promise.allSettled([users.reload(), status.reload()])
  }
}

function discard() { doc.reload() }
const isDirty = computed(() => !!doc.isDirty)
defineExpose({ isDirty, saving, save, discard })
</script>
