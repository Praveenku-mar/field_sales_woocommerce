<template>
  <!-- WooCommerce's "Order notes" box: the record's history as notes, newest first — people's
       notes in grey bubbles, the app's own notes in the system colour, field changes and
       emails — each with "by <name>, <date>" under it, and "Add note" at the bottom. Text
       only: nothing renders as markup. -->
  <SectionCard :title="title">
    <p v-if="timeline.error" class="text-[13px] text-[#b32d2e]">{{ errorText }}</p>
    <div v-else-if="timeline.loading && !items.length" class="space-y-2">
      <div v-for="i in 3" :key="i" class="h-10 animate-pulse bg-[#f0f0f1]" />
    </div>
    <p v-else-if="!items.length" class="text-[13px] text-wp-muted">There are no notes yet.</p>

    <ul class="max-h-[60vh] space-y-3 overflow-y-auto">
      <li v-for="(item, i) in shown" :key="`${item.at}-${i}`">
        <div class="relative px-2.5 py-2 text-[13px] leading-[1.5] text-[#3c434a]" :class="BUBBLE[item.kind]">
          <p v-if="item.kind === 'note' || item.kind === 'info'" class="whitespace-pre-line break-words">{{ item.text }}</p>
          <p v-else-if="item.kind === 'email'" class="break-words">{{ item.text }}: <strong class="font-semibold">{{ item.subject }}</strong></p>
          <ul v-else-if="item.kind === 'change'" class="space-y-0.5">
            <li v-for="change in item.changes" :key="change.label" class="break-words">
              {{ change.label }} changed from <em class="not-italic text-wp-muted">{{ change.from || 'blank' }}</em> to <strong class="font-semibold">{{ change.to || 'blank' }}</strong>.
            </li>
          </ul>
          <!-- the bubble's pointer, as on WooCommerce notes -->
          <span class="absolute -bottom-[10px] left-5 border-x-[10px] border-t-[10px] border-x-transparent" :class="POINTER[item.kind]" aria-hidden="true" />
        </div>
        <p class="mt-3 pl-1 text-[12px] text-wp-muted">
          <abbr class="no-underline" :title="formatDateTime(item.at)">{{ formatDateTime(item.at) }}</abbr>
          <template v-if="item.by_name"> by {{ item.by_name }}</template>
        </p>
      </li>
    </ul>
    <button v-if="items.length > LIMIT" type="button" class="wp-link mt-2 text-[13px]" @click="all = !all">
      {{ all ? 'Show fewer notes' : `Show all ${items.length} notes` }}
    </button>

    <form class="mt-4 border-t border-wp-divider pt-3" @submit.prevent="post">
      <label :for="inputId" class="mb-1 block text-[13px] font-semibold text-ink-gray-8">Add note</label>
      <textarea
        :id="inputId"
        v-model="note"
        rows="3"
        maxlength="5000"
        class="w-full rounded border border-[#8c8f94] p-2 text-[13px] focus:border-wp-blue focus:ring-1 focus:ring-wp-blue"
        @keydown.ctrl.enter.prevent="post"
        @keydown.meta.enter.prevent="post"
      />
      <div class="mt-1.5 flex items-center justify-between gap-2">
        <span class="text-[12px] text-wp-muted">Private note: your team only</span>
        <Button type="submit" label="Add" :loading="posting" :disabled="!note.trim()" />
      </div>
    </form>
  </SectionCard>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { Button, createResource, toast } from 'frappe-ui'
import SectionCard from '../SectionCard.vue'
import { callMethod } from '@/utils/api'
import { formatDateTime } from '@/utils/format'

const props = defineProps({
  doctype: { type: String, required: true },
  name: { type: String, required: true },
  /** The record's `modified` (or status): the notes reload when the record changes. */
  version: { type: String, default: null },
  title: { type: String, default: 'Notes' },
})

// WooCommerce's note colours: a person's note grey, the app's (system) notes and the rest
// in the system note colour.
const BUBBLE = { note: 'bg-[#efefef]', info: 'bg-[#d7cad2]', change: 'bg-[#d7cad2]', email: 'bg-[#a7cedc]' }
const POINTER = { note: 'border-t-[#efefef]', info: 'border-t-[#d7cad2]', change: 'border-t-[#d7cad2]', email: 'border-t-[#a7cedc]' }
const LIMIT = 8

const timeline = createResource({
  url: 'field_sales_woocommerce.chatter.get_chatter',
  // get_chatter accepts GET only; frappe-ui resources send POST unless told otherwise.
  method: 'GET',
  makeParams: () => ({ doctype: props.doctype, name: props.name }),
  auto: true,
})
watch(
  () => props.version,
  (now, before) => before && now !== before && timeline.reload(),
)

const items = computed(() => timeline.data?.items || [])
const all = ref(false)
const shown = computed(() => (all.value ? items.value : items.value.slice(0, LIMIT)))
const errorText = computed(() => String(timeline.error?.messages?.[0] || timeline.error || 'Could not load the notes'))

const inputId = `order-note-${Math.random().toString(36).slice(2, 7)}`
const note = ref('')
const posting = ref(false)

async function post() {
  const text = note.value.trim()
  if (!text || posting.value) return
  posting.value = true
  try {
    await callMethod('field_sales_woocommerce.chatter.post_note', { doctype: props.doctype, name: props.name, text })
    note.value = ''
    toast.success('Note added')
    timeline.reload()
  } catch {
    // callMethod shows the server's message; the note stays in the box.
  } finally {
    posting.value = false
  }
}
</script>
