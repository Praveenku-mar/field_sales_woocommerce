<template>
  <!-- wp-admin list table (`.wp-list-table.widefat.striped`): white header row, striped rows,
       the first column as the row's title link. In a height-bounded parent (list pages) the
       rows scroll under a sticky header so the `.tablenav` footer never covers them. -->
  <div class="flex min-h-0 flex-col gap-2">
    <div class="wp-list-table flex min-h-0 flex-col overflow-hidden border border-wp-border bg-white shadow-[0_1px_1px_rgba(0,0,0,0.04)]">
      <div class="min-h-0 overflow-auto">
        <table class="w-full text-left">
          <thead>
            <tr class="text-ink-gray-8">
              <th
                v-for="col in columns"
                :key="col.key"
                scope="col"
                class="sticky top-0 z-10 whitespace-nowrap border-b border-wp-border bg-white px-2.5 py-2 text-[14px] font-normal"
                :class="col.align === 'right' && 'text-right'"
                :style="col.width && { width: col.width }"
              >
                {{ col.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <template v-if="loading && !rows.length">
              <tr v-for="i in 5" :key="'s' + i" class="odd:bg-wp-table-alt">
                <td v-for="col in columns" :key="col.key" class="px-2.5 py-3">
                  <div class="h-3 w-3/4 animate-pulse bg-surface-gray-3" />
                </td>
              </tr>
            </template>
            <tr
              v-for="row in rows"
              :key="row.name"
              class="group odd:bg-wp-table-alt"
              :class="(rowRoute || rowClickable) && 'cursor-pointer hover:bg-[#f0f6fc]'"
              @click="rowRoute ? $router.push(rowRoute(row)) : rowClickable && $emit('row-click', row)"
            >
              <td
                v-for="(col, i) in columns"
                :key="col.key"
                class="px-2.5 py-2 align-top text-[13px] text-ink-gray-7"
                :class="[col.align === 'right' && 'text-right tabular-nums', i === 0 && 'wp-column-primary', col.class]"
              >
                <slot :name="'cell-' + col.key" :row="row" :value="row[col.key]">
                  {{ col.format ? col.format(row[col.key], row) : row[col.key] ?? '—' }}
                </slot>
                <!-- Row actions under the title, shown on hover/focus (wp-admin `.row-actions`) -->
                <div
                  v-if="i === 0 && $slots['row-actions']"
                  class="mt-0.5 text-[13px] opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 max-md:opacity-100"
                  @click.stop
                >
                  <slot name="row-actions" :row="row" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <EmptyState v-if="!loading && !rows.length" :icon="emptyIcon" :title="emptyTitle" :description="emptyDescription" />
    </div>

    <!-- `.tablenav.bottom`: item count left; rows per page and Load more right. -->
    <div v-if="rows.length" class="flex shrink-0 flex-wrap items-center justify-between gap-2 text-[13px] text-[#50575e]">
      <span class="tabular-nums">{{ formatCount(total ?? rows.length) }}<template v-if="total != null && rows.length < total">, showing {{ rows.length }}</template></span>
      <div class="flex items-center gap-2">
        <label v-if="pageLength" class="flex items-center gap-1.5">
          <span>Number of items per page:</span>
          <select
            class="h-[30px] rounded border border-[#8c8f94] bg-white py-0 pl-2 pr-7 text-[13px] text-ink-gray-8 focus:border-wp-blue focus:ring-1 focus:ring-wp-blue"
            :value="pageLength"
            @change="$emit('update:pageLength', Number($event.target.value))"
          >
            <option v-for="n in pageLengths" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
        <Button v-if="hasMore" label="Load more" :loading="loading" @click="$emit('load-more')" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { Button } from 'frappe-ui'
import EmptyState from './EmptyState.vue'

defineProps({
  /** [{ key, label, align?: 'right', width?, format?: (value, row) => string, class? }] */
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  total: { type: Number, default: null },
  hasMore: { type: Boolean, default: false },
  /** row => route location; makes rows clickable */
  rowRoute: { type: Function, default: null },
  /** Without a rowRoute, make rows clickable and emit `row-click` with the row. */
  rowClickable: { type: Boolean, default: false },
  emptyTitle: { type: String, default: 'Nothing here yet' },
  emptyDescription: { type: String, default: null },
  emptyIcon: { type: String, default: 'lucide-inbox' },
  /** Rows per page; shows the page size selector when set (use v-model:page-length). */
  pageLength: { type: Number, default: null },
  pageLengths: { type: Array, default: () => [20, 100, 500, 2500] },
})
defineEmits(['load-more', 'update:pageLength', 'row-click'])

function formatCount(n) {
  return `${new Intl.NumberFormat().format(n)} ${n === 1 ? 'item' : 'items'}`
}
</script>

<style scoped>
/* The row's title (first column) reads as a link, as in wp-admin list tables. */
.wp-column-primary :deep(.font-medium) {
  color: var(--wp-admin-theme-color);
  font-weight: 600;
}
tr:hover .wp-column-primary :deep(.font-medium) {
  color: var(--wp-admin-theme-color-darker-10);
}
</style>
