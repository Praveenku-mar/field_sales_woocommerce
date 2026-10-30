import { createResource, toast } from 'frappe-ui'
import { isEmbedded } from '@/shopify/embedded'

/** One-off server call that toasts the error message; resolves to the method's return value. */
export function callMethod(url, params = {}) {
  return createResource({ url })
    .submit(params)
    .catch((error) => {
      toast.error(error?.messages?.[0] || error?.message || 'Something went wrong')
      throw error
    })
}

/** Browser GPS fix (same options as the desk's Check In button). */
export function getPosition() {
  return new Promise((resolve, reject) => {
    // The Shopify admin's iframe is not allowed to use location (Permissions Policy).
    if (isEmbedded()) {
      reject(new Error('Location is not available inside Shopify admin. Use the Field Sales app (/fieldsales) on your phone.'))
      return
    }
    if (!window.isSecureContext || !navigator.geolocation) {
      reject(new Error('Location is not available here. Allow location access in your browser and use HTTPS.'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(position.coords),
      (error) =>
        reject(
          new Error(
            error.code === error.TIMEOUT
              ? 'Could not get a GPS fix within 15 seconds. Try again.'
              : 'Location permission was denied. Allow location access in your browser and use HTTPS.',
          ),
        ),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    )
  })
}

/**
 * The Platform Product whose barcode is exactly `code`, or null. A UPC-A code may be
 * stored as 12 digits or read as a 13-digit EAN with a leading 0, so both are tried.
 */
export async function findProductByBarcode(code, { fields = ['name'], filters = {} } = {}) {
  const variants = new Set([code])
  if (/^0\d{12}$/.test(code)) variants.add(code.slice(1))
  if (/^\d{12}$/.test(code)) variants.add(`0${code}`)
  const [product] = await createResource({ url: 'frappe.client.get_list' }).submit({
    doctype: 'Platform Product',
    fields,
    filters: { ...filters, barcode: ['in', [...variants]] },
    order_by: 'status asc, modified desc',
    limit_page_length: 1,
  })
  return product || null
}
