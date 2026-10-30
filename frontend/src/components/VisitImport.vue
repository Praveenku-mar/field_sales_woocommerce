<template>
  <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-5">
    <!-- Result of a finished import -->
    <SectionCard v-if="result" title="Import finished">
      <div class="space-y-4">
        <div class="text-sm text-ink-gray-8">
          <span class="font-medium text-ink-green-7">{{ result.created.length }} visit{{ result.created.length === 1 ? '' : 's' }} created</span>
          <span v-if="result.skipped.length">, <span class="font-medium text-ink-red-7">{{ result.skipped.length }} skipped</span></span>
        </div>
        <ul v-if="result.created.length" class="divide-y divide-outline-gray-1 rounded-4 border border-outline-gray-2">
          <li v-for="v in result.created" :key="v.name" class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <RouterLink :to="{ name: 'Visit', params: { name: v.name } }" class="font-medium text-ink-blue-6 hover:underline">{{ v.name }}</RouterLink>
            <span class="min-w-0 truncate text-ink-gray-7">{{ v.retailer_name }} · {{ formatDate(v.visit_date) }}</span>
            <span class="text-xs text-ink-gray-5">Row {{ v.row }}</span>
          </li>
        </ul>
        <ul v-if="result.skipped.length" class="divide-y divide-outline-gray-1 rounded-4 border border-outline-red-2">
          <li v-for="s in result.skipped" :key="s.row" class="px-3 py-2 text-sm text-ink-red-7">
            <span class="font-medium">Row {{ s.row }}:</span> {{ s.error }}
          </li>
        </ul>
        <Button label="Import another file" icon-left="lucide-rotate-ccw" @click="reset" />
      </div>
    </SectionCard>

    <template v-else>
      <SectionCard title="1. Download the template">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p class="text-sm text-ink-gray-6">
            Fill in one visit per row. The Lists sheet shows the sales reps, customers and purposes you can use.
          </p>
          <Button class="shrink-0" label="Download Template" icon-left="lucide-download" @click="downloadTemplate" />
        </div>
      </SectionCard>

      <SectionCard title="2. Upload your file">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p class="min-w-0 truncate text-sm text-ink-gray-6">{{ fileName || 'An .xlsx or .csv file, up to 500 rows.' }}</p>
          <FileUploader file-types=".xlsx,.csv" :private="true" @success="onUploaded">
            <template #default="{ openFileSelector, uploading, progress, error }">
              <div class="flex shrink-0 flex-col items-start gap-1 sm:items-end">
                <Button
                  :label="uploading ? `Uploading ${progress}%` : fileName ? 'Choose another file' : 'Choose File'"
                  icon-left="lucide-upload"
                  :loading="uploading || previewing"
                  @click="openFileSelector"
                />
                <span v-if="error" class="text-xs text-ink-red-7">{{ error }}</span>
              </div>
            </template>
          </FileUploader>
        </div>
        <div v-if="loadError" class="mt-3 rounded-4 bg-surface-red-2 px-3 py-2 text-sm text-ink-red-7" role="alert">{{ loadError }}</div>
      </SectionCard>

      <SectionCard v-if="preview" title="3. Check and create" flush>
        <div class="flex flex-col gap-3 border-b border-desk-border px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div class="text-sm">
            <span class="font-medium text-ink-green-7">{{ preview.ready }} ready</span>,
            <span :class="preview.errors ? 'font-medium text-ink-red-7' : 'text-ink-gray-6'">{{ preview.errors }} with errors</span>
          </div>
          <Button variant="solid" :label="`Create ${preview.ready} visits`" :disabled="!preview.ready" @click="confirmOpen = true" />
        </div>

        <!-- Phones: one card per row -->
        <ul class="divide-y divide-outline-gray-1 md:hidden">
          <li v-for="r in preview.rows" :key="r.row" class="space-y-1 px-4 py-3 text-sm">
            <div class="flex items-start justify-between gap-2">
              <span class="min-w-0 font-medium text-ink-gray-9">{{ r.retailer_name || '—' }}</span>
              <span class="shrink-0 text-xs text-ink-gray-5">Row {{ r.row }}</span>
            </div>
            <div class="text-ink-gray-7">{{ r.sales_rep_name || '—' }} · {{ r.visit_date ? formatDate(r.visit_date) : '—' }} · {{ r.visit_purpose || '—' }}</div>
            <div v-if="r.notes" class="text-xs text-ink-gray-5">{{ r.notes }}</div>
            <div v-if="r.error" class="text-ink-red-7">{{ r.error }}</div>
            <div v-else class="text-ink-green-7">✓ Ready</div>
          </li>
        </ul>

        <!-- Larger screens: table -->
        <div class="hidden max-h-[28rem] overflow-auto md:block">
          <table class="w-full text-left text-sm">
            <thead class="sticky top-0 bg-surface-gray-1 text-xs text-ink-gray-6">
              <tr>
                <th class="px-3 py-2 font-medium">Row</th>
                <th class="px-3 py-2 font-medium">Sales Rep</th>
                <th class="px-3 py-2 font-medium">Customer</th>
                <th class="px-3 py-2 font-medium">Date</th>
                <th class="px-3 py-2 font-medium">Purpose</th>
                <th class="px-3 py-2 font-medium">Notes</th>
                <th class="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-outline-gray-1">
              <tr v-for="r in preview.rows" :key="r.row" class="align-top">
                <td class="px-3 py-2 text-ink-gray-5">{{ r.row }}</td>
                <td class="px-3 py-2 text-ink-gray-8">{{ r.sales_rep_name || '—' }}</td>
                <td class="px-3 py-2 text-ink-gray-8">
                  {{ r.retailer_name || '—' }}
                  <div v-if="r.city" class="text-xs text-ink-gray-5">{{ r.city }}</div>
                </td>
                <td class="whitespace-nowrap px-3 py-2 text-ink-gray-8">{{ r.visit_date ? formatDate(r.visit_date) : '—' }}</td>
                <td class="px-3 py-2 text-ink-gray-8">{{ r.visit_purpose || '—' }}</td>
                <td class="max-w-[16rem] truncate px-3 py-2 text-ink-gray-6">{{ r.notes }}</td>
                <td class="px-3 py-2" :class="r.error ? 'text-ink-red-7' : 'text-ink-green-7'">{{ r.error || '✓' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </template>

    <Dialog v-model="confirmOpen" title="Create visits" :message="`Create ${preview?.ready || 0} planned visit(s)? Rows with errors are skipped. Each rep is notified of the visits assigned to them.`">
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Not now" :disabled="creating" @click="confirmOpen = false" />
          <Button variant="solid" :label="`Create ${preview?.ready || 0} visits`" :loading="creating" @click="create" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Button, Dialog, FileUploader } from 'frappe-ui'
import SectionCard from '@/components/SectionCard.vue'
import { callMethod } from '@/utils/api'
import { formatDate } from '@/utils/format'

const fileUrl = ref(null)
const fileName = ref('')
const preview = ref(null)
const previewing = ref(false)
const loadError = ref('')
const confirmOpen = ref(false)
const creating = ref(false)
const result = ref(null)

function downloadTemplate() {
  window.location.href = '/api/method/field_sales_woocommerce.visit_import.download_visit_template'
}

async function onUploaded(file) {
  fileUrl.value = file.file_url
  fileName.value = file.file_name
  preview.value = null
  loadError.value = ''
  previewing.value = true
  try {
    preview.value = await callMethod('field_sales_woocommerce.visit_import.preview_visit_import', { file_url: file.file_url })
  } catch (error) {
    // callMethod already toasted; keep the reason on the page as well.
    loadError.value = error?.messages?.[0] || error?.message || 'Could not read the file'
  } finally {
    previewing.value = false
  }
}

async function create() {
  creating.value = true
  try {
    result.value = await callMethod('field_sales_woocommerce.visit_import.create_visits_from_import', { file_url: fileUrl.value })
    confirmOpen.value = false
  } catch {
    // Already toasted by callMethod.
  } finally {
    creating.value = false
  }
}

function reset() {
  fileUrl.value = null
  fileName.value = ''
  preview.value = null
  loadError.value = ''
  result.value = null
}
</script>
