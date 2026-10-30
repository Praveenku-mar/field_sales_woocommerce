import frappeUIPreset, { content as frappeUIContent } from 'frappe-ui/tailwind'

export default {
  presets: [frappeUIPreset],
  content: ['./index.html', './src/**/*.{vue,js,ts}', ...frappeUIContent],
  theme: {
    extend: {
      // Frappe desk look: page background, card borders, 8px corners.
      colors: {
        desk: { bg: '#F4F5F6', border: '#EDEDED' },
      },
      width: { sidebar: '224px' },
      height: { topbar: '48px' },
    },
  },
}
