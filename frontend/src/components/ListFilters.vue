<template>
  <!-- wp-admin list screen controls: status links (`.subsubsub`) with the search box on the
       right, then the `.tablenav` row of filter dropdowns. -->
  <div class="flex flex-col gap-2">
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
      <WpSubsubsub v-if="tabs.length" v-model="tab" :options="tabs" :counts="counts" />
      <div class="flex-1" />
      <form role="search" class="flex items-center gap-1.5" @submit.prevent="commitSearch">
        <!-- Beside the search box, e.g. a barcode scan button -->
        <slot name="search-actions" />
        <label class="sr-only" :for="inputId">{{ searchPlaceholder }}</label>
        <TextInput :id="inputId" v-model="query" class="w-56" type="search" :placeholder="searchPlaceholder" />
        <Button type="submit" :label="searchLabel" />
      </form>
    </div>

    <div v-if="fields.length || isFiltered || $slots.default" class="flex flex-wrap items-center gap-2">
      <div v-for="field in fields" :key="field.fieldname" class="w-44">
        <FormControl
          v-model="values[field.fieldname]"
          :type="field.type || 'select'"
          :options="field.options"
          :placeholder="field.label"
          :aria-label="field.label"
        />
      </div>
      <Button v-if="isFiltered" label="Clear filters" @click="clear" />
      <div class="flex-1" />
      <slot />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Button, FormControl, TextInput } from 'frappe-ui'
import WpSubsubsub from './wp/WpSubsubsub.vue'

const props = defineProps({
  searchPlaceholder: { type: String, default: 'Search' },
  /** Label of the search button (wp-admin: "Search Orders"). */
  searchLabel: { type: String, default: 'Search' },
  /** Status links: [{ label, value }] */
  tabs: { type: Array, default: () => [] },
  /** { [tab value]: count } shown after each status link. */
  counts: { type: Object, default: null },
  /** Extra filters: [{ fieldname, label, type?: 'select' | 'date', options? }] */
  fields: { type: Array, default: () => [] },
})

/** { search, tab, ...fields } — the parent turns these into list filters. */
const model = defineModel({ type: Object, default: () => ({}) })

const inputId = `fs-search-${Math.random().toString(36).slice(2, 8)}`

// The list follows the box as you type (debounced); Enter or the button applies it at once.
const query = ref(model.value.search || '')
let timer = null
watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(commitSearch, 300)
})
watch(
  () => model.value.search,
  (value) => {
    if ((value || '') !== query.value) query.value = value || ''
  },
)
onBeforeUnmount(() => clearTimeout(timer))

function commitSearch() {
  clearTimeout(timer)
  if ((model.value.search || '') !== query.value) model.value = { ...model.value, search: query.value }
}

const tab = computed({
  get: () => model.value.tab ?? props.tabs[0]?.value,
  set: (value) => (model.value = { ...model.value, tab: value }),
})

const values = reactive(Object.fromEntries(props.fields.map((f) => [f.fieldname, model.value[f.fieldname] ?? ''])))
watch(values, () => (model.value = { ...model.value, ...values }))

const isFiltered = computed(() => !!model.value.search || props.fields.some((f) => values[f.fieldname]))

function clear() {
  props.fields.forEach((f) => (values[f.fieldname] = ''))
  query.value = ''
  clearTimeout(timer)
  model.value = { ...model.value, search: '', ...values }
}
</script>
