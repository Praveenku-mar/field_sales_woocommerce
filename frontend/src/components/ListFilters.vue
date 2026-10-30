<template>
  <div class="flex flex-wrap items-center gap-2">
    <TextInput
      v-model="search"
      class="w-60"
      type="text"
      :placeholder="searchPlaceholder"
      :debounce="300"
    >
      <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
    </TextInput>
    <!-- Beside the search box, e.g. a barcode scan button -->
    <slot name="search-actions" />
    <TabButtons v-if="tabs.length" v-model="tab" :options="tabs" />
    <div v-for="field in fields" :key="field.fieldname" class="w-40">
      <FormControl
        v-model="values[field.fieldname]"
        :type="field.type || 'select'"
        :options="field.options"
        :placeholder="field.label"
      />
    </div>
    <Button v-if="isFiltered" variant="ghost" label="Clear" icon-left="lucide-x" @click="clear" />
    <div class="flex-1" />
    <slot />
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { Button, FormControl, TabButtons, TextInput } from 'frappe-ui'

const props = defineProps({
  searchPlaceholder: { type: String, default: 'Search' },
  /** TabButtons options: [{ label, value }] */
  tabs: { type: Array, default: () => [] },
  /** Extra filters: [{ fieldname, label, type?: 'select' | 'date', options? }] */
  fields: { type: Array, default: () => [] },
})

/** { search, tab, ...fields } — the parent turns these into list filters. */
const model = defineModel({ type: Object, default: () => ({}) })

const search = computed({
  get: () => model.value.search || '',
  set: (value) => (model.value = { ...model.value, search: value }),
})
const tab = computed({
  get: () => model.value.tab ?? props.tabs[0]?.value,
  set: (value) => (model.value = { ...model.value, tab: value }),
})

const values = reactive(Object.fromEntries(props.fields.map((f) => [f.fieldname, model.value[f.fieldname] ?? ''])))
watch(values, () => (model.value = { ...model.value, ...values }))

const isFiltered = computed(() => !!search.value || props.fields.some((f) => values[f.fieldname]))

function clear() {
  props.fields.forEach((f) => (values[f.fieldname] = ''))
  model.value = { ...model.value, search: '', ...values }
}
</script>
