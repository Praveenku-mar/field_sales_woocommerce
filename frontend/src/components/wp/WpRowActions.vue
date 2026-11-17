<template>
  <!-- wp-admin `.row-actions`: "View | Retry sync | Trash" links under a row's title. -->
  <span class="inline-flex flex-wrap items-center text-[13px]">
    <template v-for="(action, i) in visible" :key="action.label">
      <RouterLink v-if="action.route" :to="action.route" class="wp-link" :class="action.danger && '!text-[#b32d2e]'">
        {{ action.label }}
      </RouterLink>
      <a v-else-if="action.href" :href="action.href" class="wp-link" target="_blank" rel="noopener">{{ action.label }}</a>
      <button
        v-else
        type="button"
        class="wp-link disabled:cursor-default disabled:text-[#a7aaad] disabled:no-underline"
        :class="action.danger && '!text-[#b32d2e]'"
        :disabled="action.disabled"
        @click="action.onClick"
      >
        {{ action.label }}
      </button>
      <span v-if="i < visible.length - 1" class="px-1 text-[#a7aaad]" aria-hidden="true">|</span>
    </template>
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** [{ label, route? | href? | onClick?, disabled?, danger?, hide? }] */
  actions: { type: Array, required: true },
})
const visible = computed(() => props.actions.filter((a) => !a.hide))
</script>
