/** Copy from HTTPS when available, with a fallback for local HTTP sites. */
export async function copyText(text) {
  if (!text) return false

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Some browsers or embedded pages deny clipboard permission.
    }
  }

  const field = document.createElement('textarea')
  field.value = text
  field.readOnly = true
  field.style.position = 'fixed'
  field.style.left = '-9999px'
  const previousFocus = document.activeElement
  document.body.appendChild(field)
  field.focus({ preventScroll: true })
  field.select()
  field.setSelectionRange(0, field.value.length)

  try {
    return !!document.execCommand?.('copy')
  } catch {
    return false
  } finally {
    field.remove()
    previousFocus?.focus?.({ preventScroll: true })
  }
}
