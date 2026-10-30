import { computed } from 'vue'
import { createResource } from 'frappe-ui'

// One shared fetch: every order screen needs the same short list.
const resource = createResource({ url: 'field_sales_woocommerce.api.get_sell_locations', cache: 'sell_locations', auto: false })

/**
 * Locations the shop sells from (Store Connection), default first.
 * `locations` rows are { location_id, location_name, is_default }; `options` are select options
 * (value = location id); `multiple` is true when a rep has a real choice.
 */
export function useSellLocations() {
  if (!resource.data && !resource.loading) resource.fetch()
  const locations = computed(() => resource.data || [])
  const options = computed(() => locations.value.map((l) => ({ label: l.location_name, value: l.location_id })))
  const defaultLocation = computed(() => locations.value.find((l) => l.is_default) || locations.value[0] || null)
  const multiple = computed(() => locations.value.length > 1)
  return { locations, options, defaultLocation, multiple, loading: computed(() => resource.loading) }
}
