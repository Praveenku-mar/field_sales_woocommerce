<template>
  <!-- "Howdy, <name>" in the admin bar, with Switch to Desk and Log out. Phones show the
       avatar only. -->
  <Dropdown :options="options" side="bottom" align="end">
    <button
      type="button"
      class="flex h-8 items-center gap-2 px-2 text-left text-[13px] text-wp-menu-text hover:bg-wp-menu-sub hover:text-[#72aee6] focus:outline-none focus-visible:bg-wp-menu-sub"
      :aria-label="`Account: ${session.fullName}`"
    >
      <span class="hidden truncate md:inline">Howdy, {{ session.fullName }}</span>
      <Avatar :label="session.fullName" :image="session.userImage" size="sm" />
    </button>
  </Dropdown>

  <Dialog v-model="iosHintOpen" title="Add to Home Screen" size="sm">
    <p class="text-p-base text-ink-gray-7">
      In Safari, tap the Share button, then choose Add to Home Screen to install Agamsale.
    </p>
  </Dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Avatar, Dialog, Dropdown } from 'frappe-ui'
import { useInstallPrompt } from '@/composables/useInstallPrompt'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const { canInstall, showIosHint, install } = useInstallPrompt()
const iosHintOpen = ref(false)

const options = computed(() => [
  {
    group: session.user,
    options: [
      canInstall.value && { label: 'Install app', icon: 'lucide-download', onClick: install },
      showIosHint.value && { label: 'Add to Home Screen', icon: 'lucide-download', onClick: () => (iosHintOpen.value = true) },
      // A customer login is a website user: the desk would turn it away.
      !session.isRetailer && { label: 'Switch to Desk', icon: 'lucide-app-window', onClick: () => (window.location.href = '/app') },
      { label: 'Log out', icon: 'lucide-log-out', onClick: () => session.logout() },
    ].filter(Boolean),
  },
])
</script>
