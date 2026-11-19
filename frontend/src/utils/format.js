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
  // Frappe sends "YYYY-MM-DD" or "YYYY-MM-DD HH:mm:ss(.ffffff)" in system time: read both as
  // wall-clock values. A bare "YYYY-MM-DD" would otherwise be parsed as UTC midnight, which is
  // the previous day in any browser west of UTC.
  const text = String(value).replace(' ', 'T').slice(0, 19)
  return new Date(text.length === 10 ? `${text}T00:00:00` : text)
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

/**
 * The current date and time on the site's clock: its time zone (System Settings, which follows the
 * store's when Match Store Currency and Timezone is on), not the browser's. Server dates and times
 * are naive values in that zone, so "today" and expiry checks must use it too, or they shift by a
 * day for a browser elsewhere. The browser's own zone is used only when the site's is unknown.
 * @returns {{year: number, month: number, day: number, hour: number, minute: number, second: number}}
 */
export function siteNow() {
  const now = new Date()
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat('en-US', {
        timeZone: window.time_zone || undefined,
        hourCycle: 'h23',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
        .formatToParts(now)
        .filter((p) => p.type !== 'literal')
        .map((p) => [p.type, Number(p.value)]),
    )
    return { year: parts.year, month: parts.month, day: parts.day, hour: parts.hour, minute: parts.minute, second: parts.second }
  } catch {
    // An unknown zone name: fall back to the browser's clock.
    return {
      year: now.getFullYear(),
      month: now.getMonth() + 1,
      day: now.getDate(),
      hour: now.getHours(),
      minute: now.getMinutes(),
      second: now.getSeconds(),
    }
  }
}

const pad2 = (n) => String(n).padStart(2, '0')

/** Now on the site's clock as the server writes it: "YYYY-MM-DD HH:MM:SS". */
export function nowSql() {
  const n = siteNow()
  return `${n.year}-${pad2(n.month)}-${pad2(n.day)} ${pad2(n.hour)}:${pad2(n.minute)}:${pad2(n.second)}`
}

/**
 * Milliseconds from now until a server datetime ("YYYY-MM-DD HH:MM:SS", site time); negative once
 * it has passed. Both are read as wall-clock times on the site's clock, so the browser's zone does
 * not matter. `null` for an empty or unreadable value.
 */
export function msUntil(value) {
  const m = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (!m) return null
  const [, y, mo, d, h, mi, s] = m.map(Number)
  const n = siteNow()
  return Date.UTC(y, mo - 1, d, h, mi, s || 0) - Date.UTC(n.year, n.month - 1, n.day, n.hour, n.minute, n.second)
}

/** Whether a server datetime ("YYYY-MM-DD HH:MM:SS[.ffffff]", site time) has passed. */
export function isPast(value) {
  return !!value && String(value).slice(0, 19) <= nowSql()
}

/** Whole days from `value` until today on the site's clock (0 = today). */
export function daysAgo(value) {
  const date = toDate(value)
  if (!date) return null
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const t = siteNow()
  return Math.round((new Date(t.year, t.month - 1, t.day) - start(date)) / 86400000)
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

/** Today's date on the site's clock, "YYYY-MM-DD" (e.g. the Visits page's Today tab). */
export function today() {
  const t = siteNow()
  return `${t.year}-${pad2(t.month)}-${pad2(t.day)}`
}

/** "Draft" for unsubmitted orders, "Cancelled" for cancelled, else the sync state. */
export function orderStatus(order) {
  if (order.docstatus === 0) return 'Draft'
  if (order.docstatus === 2) return 'Cancelled'
  return order.sync_status || 'Pending'
}
