<template>
  <div>
    <PageHeader title="Reports" />
    <div class="px-5 pb-6">
      <SectionCard flush>
        <ul v-if="reports.length" class="divide-y divide-outline-gray-2">
          <li v-for="report in reports" :key="report.label">
            <RouterLink
              :to="report.route"
              class="flex items-center gap-3 px-4 py-3 hover:bg-surface-gray-2 focus:outline-none focus-visible:bg-surface-gray-2"
            >
              <span :class="report.icon" class="size-4 shrink-0 text-ink-gray-5" aria-hidden="true" />
              <span class="min-w-0 flex-1">
                <span class="block font-medium text-ink-gray-9">{{ report.label }}</span>
                <span class="block truncate text-ink-gray-5">{{ report.description }}</span>
              </span>
              <span class="lucide-chevron-right size-4 shrink-0 text-ink-gray-4" aria-hidden="true" />
            </RouterLink>
          </li>
        </ul>
        <EmptyState v-else icon="lucide-chart-column" title="No reports" description="Reports you can open appear here." />
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useSessionStore } from '@/stores/session'
import { REPORT_LINKS } from '@/utils/nav'

const session = useSessionStore()

const reports = computed(() => REPORT_LINKS.filter((r) => session.canSee(r)))
</script>
