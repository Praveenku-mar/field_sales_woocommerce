import frappeUIPreset, { content as frappeUIContent } from 'frappe-ui/tailwind'

export default {
  presets: [frappeUIPreset],
  content: ['./index.html', './src/**/*.{vue,js,ts}', ...frappeUIContent],
  theme: {
    extend: {
      // wp-admin look: page background and postbox borders (src/theme/wp-admin.css has the rest).
      colors: {
        desk: { bg: '#F0F0F1', border: '#C3C4C7' },
        wp: {
          blue: '#2271B1',
          'blue-dark': '#135E96',
          menu: '#1D2327',
          'menu-sub': '#2C3338',
          'menu-text': '#F0F0F1',
          'menu-muted': '#A7AAAD',
          muted: '#646970',
          divider: '#DCDCDE',
          'table-alt': '#F6F7F7',
          red: '#D63638',
        },
      },
      width: { sidebar: '160px' },
      height: { topbar: '46px', adminbar: '32px' },
    },
  },
}
