<template>
  <Combobox
    :model-value="value"
    :options="options"
    :placeholder="placeholder"
    :disabled="disabled"
    :loading="search.loading"
    :filterable="false"
    open-on-focus
    @update:model-value="onPick"
    @update:open="onOpen"
    @update:query="onQuery"
  />
</template>

<script setup>
import { computed, ref } from 'vue'
import { Combobox, createResource, debounce } from 'frappe-ui'

/**
 * A Link field: searches `doctype` on the server (under the user's permissions) as they type.
 * `titleField` labels each option, `descriptionField` shows next to it in the dropdown.
 */
const props = defineProps({
  doctype: { type: String, required: true },
  titleField: { type: String, default: 'name' },
  /** Option label from a row; overrides `titleField`. */
  labelOf: { type: Function, default: null },
  descriptionField: { type: String, default: null },
  /** Extra fields fetched with each row; emitted with `select`. */
  fields: { type: Array, default: () => [] },
  searchFields: { type: Array, default: () => ['name'] },
  filters: { type: [Object, Array], default: () => ({}) },
  /** Label for the current value before it has been fetched. */
  label: { type: String, default: null },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  /** Values to leave out (e.g. products already on the order). */
  exclude: { type: Array, default: () => [] },
})
const emit = defineEmits(['select'])
const value = defineModel({ type: String, default: null })

const rows = ref([])
const search = createResource({
  url: 'frappe.client.get_list',
  makeParams: (txt) => ({
    doctype: props.doctype,
    fields: [...new Set(['name', props.titleField, props.descriptionField, ...props.fields].filter(Boolean))],
    filters: props.filters,
    or_filters: txt ? props.searchFields.map((field) => [field, 'like', `%${txt}%`]) : null,
    order_by: 'modified desc',
    limit_page_length: 20,
  }),
  onSuccess: (data) => (rows.value = data),
})

const toOption = (row) => ({
  label: (props.labelOf ? props.labelOf(row) : row[props.titleField]) || row.name,
  value: row.name,
  description: props.descriptionField ? row[props.descriptionField] : undefined,
  row,
})
const selected = ref(null)
const options = computed(() => {
  const list = rows.value.filter((row) => !props.exclude.includes(row.name) || row.name === value.value).map(toOption)
  // The current value stays selectable (and labelled) even when the search no longer returns it.
  if (value.value && !list.some((o) => o.value === value.value)) {
    list.unshift(selected.value?.value === value.value ? selected.value : { label: props.label || value.value, value: value.value })
  }
  return list
})

// Only a pick by the user emits `select`, so loading a saved value never overwrites fetched data.
function onPick(name) {
  const option = options.value.find((o) => o.value === name)
  if (option) selected.value = option
  value.value = name
  if (name) emit('select', option?.row || { name })
}

// Search lazily: a table of product pickers should not query the server until one is opened.
let searched = false
function onOpen(open) {
  if (open && !searched) {
    searched = true
    search.submit('')
  }
}
const onQuery = debounce((txt) => {
  searched = true
  search.submit(txt)
}, 250)
</script>
