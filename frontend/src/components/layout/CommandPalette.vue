<template>
  <Dialog v-model="open" size="xl" position="top" bare>
    <div class="overflow-hidden rounded-4 bg-white">
      <div class="flex items-center gap-2 border-b border-desk-border px-3">
        <span class="lucide-search size-4 text-ink-gray-5" />
        <input
          ref="input"
          v-model="query"
          class="h-11 flex-1 border-0 bg-transparent p-0 text-base focus:ring-0"
          placeholder="Jump to a customer or order…"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="go(results[active])"
        />
        <LoadingIndicator v-if="search.loading" class="size-4 text-ink-gray-5" />
      </div>

      <div class="max-h-96 overflow-y-auto p-1.5">
        <div v-if="!query.trim()" class="px-3 py-6 text-center text-ink-gray-5">
          Type a customer name, city, phone or order number
        </div>
        <div v-else-if="!results.length && !search.loading" class="px-3 py-6 text-center text-ink-gray-5">
          No matches for “{{ query }}”
        </div>
        <template v-for="(group, label) in grouped" :key="label">
          <div v-if="group.length" class="px-2 pb-1 pt-2 text-xs font-medium text-ink-gray-5">{{ label }}</div>
          <button
            v-for="item in group"
            :key="item.key"
            class="flex w-full items-center gap-2 rounded-3 px-2 py-1.5 text-left"
            :class="results[active] === item ? 'bg-surface-gray-3' : 'hover:bg-surface-gray-2'"
            @mouseenter="active = results.indexOf(item)"
            @click="go(item)"
          >
            <span :class="[item.icon, 'size-4 text-ink-gray-5']" />
            <span class="flex-1 truncate">
              <span class="text-ink-gray-9">{{ item.title }}</span>
              <span class="ml-2 text-ink-gray-5">{{ item.subtitle }}</span>
            </span>
            <StatusPill v-if="item.status" :status="item.status" />
          </button>
        </template>
      </div>
    </div>
  </Dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Dialog, LoadingIndicator, createResource } from 'frappe-ui'
import StatusPill from '@/components/StatusPill.vue'

const open = defineModel({ type: Boolean, default: false })
const router = useRouter()
const query = ref('')
const active = ref(0)
const input = ref(null)

const search = createResource({ url: 'field_sales_woocommerce.frontend_api.search' })

let timer
watch(query, (txt) => {
  clearTimeout(timer)
  active.value = 0
  if (!txt.trim()) return search.reset()
  timer = setTimeout(() => search.submit({ txt }), 200)
})

watch(open, async (isOpen) => {
  if (!isOpen) return
  query.value = ''
  await nextTick()
  input.value?.focus()
})

const grouped = computed(() => {
  const data = search.data || {}
  return {
    Customers: (data.retailers || []).map((r) => ({
      key: 'r:' + r.name,
      icon: 'lucide-store',
      title: r.retailer_name,
      subtitle: [r.name, r.city].filter(Boolean).join(' · '),
      route: { name: 'Retailer', params: { name: r.name } },
    })),
    'Field Orders': (data.orders || []).map((o) => ({
      key: 'o:' + o.name,
      icon: 'lucide-shopping-cart',
      title: o.name,
      subtitle: o.retailer_name,
      status: o.docstatus === 0 ? 'Draft' : o.sync_status,
      route: { name: 'Order', params: { name: o.name } },
    })),
  }
})
const results = computed(() => Object.values(grouped.value).flat())

function move(step) {
  if (!results.value.length) return
  active.value = (active.value + step + results.value.length) % results.value.length
}

function go(item) {
  if (!item) return
  open.value = false
  router.push(item.route)
}
</script>
