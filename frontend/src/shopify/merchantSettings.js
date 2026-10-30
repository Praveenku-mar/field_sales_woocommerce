// Inside the Shopify admin the embedded user is a manager, not a System Manager, so the Settings
// forms can't read or write Store Connection / Email Account directly. These stand-ins have the
// same shape as frappe-ui's createDocumentResource / createListResource and go through
// field_sales.shopify.merchant_settings, which only accepts the merchant-facing fields.
import { reactive, watch } from 'vue'
import { createResource } from 'frappe-ui'

const API = 'field_sales_woocommerce.shopify.merchant_settings'

function request(method, params = {}, httpMethod = 'POST') {
  return createResource({ url: `${API}.${method}`, method: httpMethod }).submit(params)
}

/** Store Connection as the forms use it: doc, get.{loading,error}, save.submit(), reload(), isDirty. */
export function createMerchantStoreDoc() {
  let snapshot = ''
  const state = reactive({
    doc: null,
    isDirty: false,
    get: { loading: false, error: null },
    save: { loading: false, submit },
    reload,
  })

  function setDoc(doc) {
    state.doc = doc
    snapshot = JSON.stringify(doc)
    state.isDirty = false
  }

  async function reload() {
    state.get.loading = true
    try {
      setDoc(await request('get_store_settings', {}, 'GET'))
      state.get.error = null
    } catch (error) {
      state.get.error = error
    } finally {
      state.get.loading = false
    }
  }

  async function submit() {
    state.save.loading = true
    try {
      const saved = await request('save_store_settings', { values: state.doc })
      setDoc(saved)
      return saved
    } finally {
      state.save.loading = false
    }
  }

  watch(
    () => state.doc,
    (doc) => {
      state.isDirty = !!doc && JSON.stringify(doc) !== snapshot
    },
    { deep: true },
  )
  reload()
  return state
}

/** Email accounts as EmailAccountSettings uses them: data, list.{loading,error}, reload(). */
export function createMerchantEmailAccounts() {
  const state = reactive({ data: null, list: { loading: false, error: null }, reload })
  async function reload() {
    state.list.loading = true
    try {
      state.data = await request('get_email_accounts', {}, 'GET')
      state.list.error = null
    } catch (error) {
      state.list.error = error?.messages?.[0] || error?.message || 'Could not load email accounts'
    } finally {
      state.list.loading = false
    }
  }
  reload()
  return state
}

export const merchantLocationsUrl = `${API}.fetch_locations`
export const saveMerchantEmailAccountUrl = `${API}.save_email_account`
