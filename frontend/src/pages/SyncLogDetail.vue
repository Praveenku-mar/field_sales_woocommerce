<template>
  <div>
    <PageHeader :title="d.operation ? `${d.operation}${d.reference_name ? ` · ${d.reference_name}` : ''}` : name" :status="d.status || null">
      <template #meta><span class="text-ink-gray-5">{{ d.creation ? formatDateTime(d.creation) : '' }}</span></template>
      <template #actions>
        <Button v-if="referenceRoute" :label="`Open ${d.reference_doctype}`" icon-left="lucide-arrow-up-right" :route="referenceRoute" />
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Sync log entry not found" :description="errorText" /></div></div>

    <div v-else class="grid grid-cols-1 items-start gap-4 px-5 pb-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <div class="min-w-0 space-y-4">
        <div v-if="d.error_message || d.error_code" class="flex gap-2 rounded-4 bg-surface-red-2 px-4 py-3 text-ink-red-6">
          <span class="lucide-circle-alert mt-px size-4 shrink-0" />
          <div class="min-w-0 break-words">
            <div class="font-semibold">
              {{ d.status === 'Retrying' ? 'Failed, will retry' : 'Failed' }}
              <span v-if="d.error_code" class="ml-1 font-mono text-xs font-normal">{{ d.error_code }}</span>
            </div>
            <div v-if="d.error_message" class="whitespace-pre-wrap">{{ d.error_message }}</div>
          </div>
        </div>

        <SectionCard title="Details">
          <InfoGrid :items="details" :columns="3" />
        </SectionCard>

        <SectionCard v-for="p in payloads" :key="p.label" :title="p.label" flush>
          <template #actions>
            <Button v-if="p.text" variant="ghost" size="sm" :label="copied === p.label ? 'Copied' : 'Copy'" :icon-left="copied === p.label ? 'lucide-check' : 'lucide-copy'" @click="copy(p)" />
          </template>
          <pre v-if="p.text" class="max-h-96 overflow-auto bg-surface-gray-1 p-4 font-mono text-xs leading-relaxed text-ink-gray-8">{{ p.text }}</pre>
          <div v-else class="px-4 py-5 text-ink-gray-5">Nothing recorded</div>
        </SectionCard>
      </div>

      <div class="fs-card">
        <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
          <span class="lucide-history size-4 text-ink-gray-7" />
          <h2 class="flex-1 text-base font-semibold text-ink-gray-9">{{ d.reference_name ? 'All Attempts' : 'Recent Entries' }}</h2>
          <span class="text-ink-gray-5">{{ history.data?.length || '' }}</span>
        </div>
        <div v-if="history.list.loading && !history.data" class="flex justify-center py-6"><LoadingIndicator class="size-4" /></div>
        <ol v-else class="divide-y divide-outline-gray-1">
          <li v-for="row in history.data || []" :key="row.name">
            <RouterLink
              :to="{ name: 'SyncLogEntry', params: { name: row.name } }"
              class="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-gray-1"
              :class="row.name === name && 'bg-surface-gray-2'"
            >
              <div class="min-w-0 flex-1">
                <div class="truncate text-ink-gray-9">{{ row.operation }}<span v-if="row.attempt" class="text-ink-gray-5"> · #{{ row.attempt }}</span></div>
                <div class="text-xs text-ink-gray-5">{{ relativeDay(row.creation) }}, {{ formatTime(row.creation) }}</div>
              </div>
              <StatusPill :status="row.status" />
            </RouterLink>
          </li>
        </ol>
        <div v-if="d.reference_name" class="p-4">
          <Button class="w-full" label="View all in Sync Log" :route="{ name: 'SyncLogs', query: { reference: d.reference_name } }" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Button, LoadingIndicator, createDocumentResource, createListResource, toast } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import InfoGrid from '@/components/InfoGrid.vue'
import StatusPill from '@/components/StatusPill.vue'
import EmptyState from '@/components/EmptyState.vue'
import { formatDateTime, formatNumber, formatTime, relativeDay } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })

const doc = createDocumentResource({ doctype: 'Sync Log', name: props.name, auto: true })
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))

// Records with a page in this app; anything else opens in the desk from the details.
const ROUTES = { 'Field Order': 'Order', Retailer: 'Retailer', 'Platform Product': 'Product', Visit: 'Visit' }
const referenceRoute = computed(() => {
  const routeName = ROUTES[d.value.reference_doctype]
  return routeName && d.value.reference_name ? { name: routeName, params: { name: d.value.reference_name } } : null
})

const details = computed(() => [
  { label: 'Platform', value: d.value.platform },
  { label: 'Operation', value: d.value.operation },
  {
    label: d.value.reference_doctype || 'Record',
    value: d.value.reference_name,
    route: referenceRoute.value,
    href: !referenceRoute.value && d.value.reference_name
      ? `/app/${d.value.reference_doctype.toLowerCase().replaceAll(' ', '-')}/${encodeURIComponent(d.value.reference_name)}`
      : null,
  },
  { label: 'Attempt', value: d.value.attempt ? `#${d.value.attempt}` : null },
  { label: 'HTTP Status', value: d.value.http_status || null },
  { label: 'Duration', value: d.value.duration_ms == null ? null : `${formatNumber(d.value.duration_ms)} ms` },
  { label: 'External ID', value: d.value.external_id },
  { label: 'Endpoint', value: d.value.endpoint, hide: !d.value.endpoint },
])

/** Payloads are stored as JSON text; show them indented, or as-is when they are not JSON. */
function pretty(text) {
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}
const payloads = computed(() => [
  { label: 'Request', text: pretty(d.value.request_payload) },
  { label: 'Response', text: pretty(d.value.response_payload) },
])

const copied = ref(null)
async function copy(p) {
  try {
    await navigator.clipboard.writeText(p.text)
    copied.value = p.label
    setTimeout(() => (copied.value = null), 1500)
  } catch {
    toast.error('Could not copy; select the text instead')
  }
}

// Every entry for the same record (oldest attempt last), or the latest entries when there is none.
const history = createListResource({
  doctype: 'Sync Log',
  fields: ['name', 'creation', 'operation', 'status', 'attempt'],
  orderBy: 'creation desc',
  pageLength: 20,
})
watch(
  () => d.value.name,
  (loaded) => {
    if (!loaded) return
    history.filters = d.value.reference_name
      ? { reference_doctype: d.value.reference_doctype, reference_name: d.value.reference_name }
      : { reference_name: ['is', 'not set'] }
    history.reload()
  },
  { immediate: true },
)
</script>
