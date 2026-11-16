<template>
  <!-- wp-admin `.notice`: white strip with a coloured left border; optionally dismissible. -->
  <div
    v-if="open"
    class="flex items-start gap-2 border border-wp-divider border-l-4 bg-white px-3 py-2 shadow-[0_1px_1px_rgba(0,0,0,0.04)]"
    :class="BORDERS[type]"
    :role="type === 'error' ? 'alert' : 'status'"
  >
    <div class="min-w-0 flex-1 py-0.5 text-[13px] leading-[1.5] text-ink-gray-8">
      <slot>{{ message }}</slot>
    </div>
    <button
      v-if="dismissible"
      type="button"
      class="-mr-1 shrink-0 p-1 text-[#787c82] hover:text-wp-red focus:outline-none focus-visible:ring-2 focus-visible:ring-wp-blue"
      aria-label="Dismiss this notice"
      @click="dismiss"
    >
      <span class="lucide-circle-x size-4" aria-hidden="true" />
    </button>
  </div>
</template>

<script setup>
import { ref } from 'vue'

defineProps({
  /** success | error | warning | info */
  type: { type: String, default: 'info' },
  message: { type: String, default: null },
  dismissible: { type: Boolean, default: false },
})
const emit = defineEmits(['dismiss'])

const BORDERS = {
  success: 'border-l-[#00a32a]',
  error: 'border-l-[#d63638]',
  warning: 'border-l-[#dba617]',
  info: 'border-l-[#72aee6]',
}

const open = ref(true)
function dismiss() {
  open.value = false
  emit('dismiss')
}
</script>
