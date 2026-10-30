<template>
  <!-- In a height-bounded parent (list pages) the rows scroll inside the card under a
       sticky header, so the footer below never covers them. -->
  <div class="fs-card flex min-h-0 flex-col overflow-hidden">
    <div class="min-h-0 overflow-auto">
      <table class="w-full text-left">
        <thead>
          <tr class="border-b border-desk-border bg-surface-gray-1 text-ink-gray-5">
            <th
              v-for="col in columns"
              :key="col.key"
              class="sticky top-0 z-10 whitespace-nowrap bg-surface-gray-1 px-4 py-2 text-xs font-medium"
              :class="col.align === 'right' && 'text-right'"
              :style="col.width && { width: col.width }"
            >
              {{ col.label }}
            </th>
          </tr>
        </thead>
        <tbody>
          <template v-if="loading && !rows.length">
            <tr v-for="i in 5" :key="'s' + i" class="border-b border-desk-border last:border-0">
              <td v-for="col in columns" :key="col.key" class="px-4 py-3">
                <div class="h-3 w-3/4 animate-pulse rounded-3 bg-surface-gray-2" />
              </td>
            </tr>
          </template>
          <tr
            v-for="row in rows"
            :key="row.name"
            class="border-b border-desk-border last:border-0"
            :class="(rowRoute || rowClickable) && 'cursor-pointer hover:bg-surface-gray-1'"
            @click="rowRoute ? $router.push(rowRoute(row)) : rowClickable && $emit('row-click', row)"
          >
            <td
              v-for="col in columns"
              :key="col.key"
              class="px-4 py-2.5 text-ink-gray-8"
              :class="[col.align === 'right' && 'text-right tabular-nums', col.class]"
            >
              <slot :name="'cell-' + col.key" :row="row" :value="row[col.key]">
                {{ col.format ? col.format(row[col.key], row) : row[col.key] ?? '—' }}
              </slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <EmptyState v-if="!loading && !rows.length" :icon="emptyIcon" :title="emptyTitle" :description="emptyDescription" />

    <div
      v-if="rows.length"
      class="flex shrink-0 items-center justify-between border-t border-desk-border px-4 py-2 text-xs text-ink-gray-5"
    >
      <div class="flex items-center gap-3">
        <!-- Rows per page, as in the desk list footer (v-model:page-length) -->
        <TabButtons
          v-if="pageLength"
          size="sm"
          aria-label="Rows per page"
          :model-value="pageLength"
          :options="pageLengths.map((n) => ({ label: String(n), value: n }))"
          @update:model-value="$emit('update:pageLength', $event)"
        />
        <span>{{ rows.length }} of {{ total ?? rows.length }}</span>
      </div>
      <Button v-if="hasMore" label="Load More" size="sm" :loading="loading" @click="$emit('load-more')" />
    </div>
  </div>
</template>

<script setup>
import { Button, TabButtons } from 'frappe-ui'
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
</script>
