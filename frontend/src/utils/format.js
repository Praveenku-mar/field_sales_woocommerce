// Display helpers. Currency comes from the site defaults (System Settings), like the desk.

export function siteCurrency() {
  return window.sysdefaults?.currency || 'INR'
}

export function formatCurrency(value, currency = siteCurrency(), digits = 2) {
  if (value == null || value === '') return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(value)
  } catch {
    return `${currency} ${Number(value).toFixed(digits)}`
  }
}

export function formatNumber(value, digits = 0) {
  if (value == null || value === '') return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value)
}

function toDate(value) {
  if (!value) return null
  // Frappe sends "YYYY-MM-DD" or "YYYY-MM-DD HH:mm:ss(.ffffff)" in system time.
  return new Date(String(value).replace(' ', 'T').slice(0, 19))
}

export function formatDate(value) {
  const date = toDate(value)
  return date ? date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'
}

export function formatTime(value) {
  const date = toDate(value)
  return date ? date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : '—'
}

export function formatDateTime(value) {
  return value ? `${formatDate(value)}, ${formatTime(value)}` : '—'
}

/** Whole days from `value` until today (0 = today). */
export function daysAgo(value) {
  const date = toDate(value)
  if (!date) return null
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
  return Math.round((start(new Date()) - start(date)) / 86400000)
}

export function relativeDay(value) {
  const days = daysAgo(value)
  if (days === null) return 'Never'
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days < 0) return `In ${-days} days`
  return `${days} days ago`
}

/** "1 order", "3 orders". */
export function plural(count, word, many = `${word}s`) {
  return `${formatNumber(count)} ${count === 1 ? word : many}`
}

export function today() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** "Draft" for unsubmitted orders, "Cancelled" for cancelled, else the sync state. */
export function orderStatus(order) {
  if (order.docstatus === 0) return 'Draft'
  if (order.docstatus === 2) return 'Cancelled'
  return order.sync_status || 'Pending'
}
