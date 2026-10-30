<template>
  <div>
    <PageHeader :title="name" :status="d.name ? orderStatus(d) : null">
      <template #actions>
        <Dropdown v-if="d.name" :options="moreActions" align="end">
          <Button variant="ghost" icon="lucide-ellipsis" aria-label="More actions" />
        </Dropdown>
        <Button
          v-if="d.name"
          variant="ghost"
          label="Preview"
          icon-left="lucide-eye"
          :href="`/printview?doctype=Field%20Order&name=${encodeURIComponent(name)}&format=Field%20Sales%20Order`"
          target="_blank"
        />
        <template v-if="editable">
          <Button v-if="dirty" label="Save" variant="solid" :loading="saving" @click="save" />
          <Button v-else label="Submit" variant="solid" :loading="saving" @click="confirmSubmit" />
        </template>
      </template>
    </PageHeader>

    <div v-if="doc.get.error" class="px-5"><div class="fs-card"><EmptyState icon="lucide-circle-alert" title="Order not found" :description="errorText" /></div></div>

    <div v-else-if="!form" class="flex justify-center py-16 text-ink-gray-5"><LoadingIndicator class="size-5" /></div>

    <div v-else class="grid grid-cols-1 items-start gap-4 px-5 pb-6 xl:grid-cols-[minmax(0,1fr)_340px]">
      <!-- Order -->
      <div class="fs-card">
        <div class="grid grid-cols-1 gap-4 border-b border-desk-border p-5 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Customer" required>
            <LinkField
              v-if="editable"
              v-model="form.retailer"
              doctype="Retailer"
              title-field="retailer_name"
              description-field="city"
              :search-fields="['name', 'retailer_name', 'city', 'phone']"
              :filters="{ status: 'Active' }"
              :label="form.retailer_name"
              placeholder="Select customer"
              @select="onRetailer"
            />
            <ReadOnly v-else :value="form.retailer_name || form.retailer" :aside="retailerCity.data?.city" :route="{ name: 'Retailer', params: { name: form.retailer } }" />
          </Field>
          <Field label="Visit">
            <LinkField
              v-if="editable"
              :key="form.retailer"
              v-model="form.visit"
              doctype="Visit"
              description-field="visit_date"
              :filters="form.retailer ? { retailer: form.retailer } : {}"
              placeholder="Select visit"
            />
            <ReadOnly v-else :value="form.visit" :route="form.visit && { name: 'Visit', params: { name: form.visit } }" />
          </Field>
          <Field label="Date" required>
            <DatePicker v-if="editable" v-model="form.order_date" />
            <ReadOnly v-else :value="formatDate(form.order_date)" />
          </Field>
          <Field label="Location">
            <Select
              v-if="editable && multipleLocations"
              :model-value="form.location_id"
              :options="locationOptions"
              :disabled="locationBusy"
              @update:model-value="changeLocation"
            />
            <ReadOnly v-else :value="locationName" />
          </Field>
        </div>

        <!-- Items -->
        <div class="space-y-3 border-b border-desk-border p-5">
          <div class="flex items-center gap-2">
            <h2 class="text-base font-semibold text-ink-gray-9">Items</h2>
            <span class="text-ink-gray-5">{{ form.items.length }}</span>
            <Button
              v-if="editable && selected.size"
              class="ml-auto"
              theme="red"
              :label="`Delete ${selected.size} ${selected.size === 1 ? 'row' : 'rows'}`"
              icon-left="lucide-trash-2"
              @click="deleteSelected"
            />
          </div>

          <!-- Search-to-add: matches the catalog on the server, picking adds the row.
               The results panel is in flow — it makes room instead of overlapping
               the items table. -->
          <div v-if="editable">
            <div class="flex items-center gap-2">
              <TextInput
                ref="quickSearchEl"
                v-model="quickQuery"
                class="flex-1"
                placeholder="Search a product to add — name, SKU or barcode"
                @keydown.enter.prevent="quickRows.length && quickAdd(quickRows[0])"
                @blur="quickOpen = false"
                @focus="quickSearch.data && (quickOpen = true)"
              >
                <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
              </TextInput>
              <!-- No camera inside Shopify admin (iframe) -->
          <Button v-if="!embedded" icon="lucide-scan-barcode" tooltip="Scan barcode" aria-label="Scan barcode" @click="scanOpen = true" />
            </div>
            <ul
              v-if="quickOpen && (quickSearch.loading || quickRows.length)"
              class="mt-1 max-h-72 overflow-y-auto rounded-4 border border-outline-gray-2 bg-surface-base py-1"
            >
              <li v-if="quickSearch.loading" class="flex justify-center px-3 py-3"><LoadingIndicator class="size-4 text-ink-gray-5" /></li>
              <li
                v-for="p in quickRows"
                :key="p.name"
                class="flex cursor-pointer items-center gap-3 px-3 py-2 text-sm hover:bg-surface-gray-1"
                @mousedown.prevent="quickAdd(p)"
                @mouseenter="quickActive = p.name"
                @mouseleave="quickActive = null"
                :class="quickActive === p.name && 'bg-surface-gray-1'"
              >
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-ink-gray-9">{{ productLabel(p) }}</span>
                  <span class="block truncate font-mono text-xs text-ink-gray-5">{{ p.sku || p.name }}</span>
                </span>
                <span class="shrink-0 text-right">
                  <span class="block tabular-nums">{{ formatCurrency(p.price) }}</span>
                  <span class="block text-xs" :class="p.track_inventory && (listedQty(p) ?? 1) <= 0 ? 'text-ink-red-5' : 'text-ink-gray-5'">
                    {{ stockText(p, 'in stock', 'not tracked') }}
                  </span>
                </span>
              </li>
            </ul>
          </div>

          <div v-if="warningRows.length" class="flex items-start gap-2 rounded-4 bg-surface-red-2 px-3 py-2.5 text-ink-red-6">
            <span class="lucide-triangle-alert mt-px size-4 shrink-0" />
            <span>
              <b>{{ warningRows.length }} {{ warningRows.length === 1 ? 'item exceeds' : 'items exceed' }} available stock</b>
              <template v-if="syncInfo.data?.location_name"> at {{ syncInfo.data.location_name }}</template>.
              {{ editable ? 'Reduce the quantity or split the order before submitting.' : 'Cancel the order and amend it with lower quantities.' }}
            </span>
          </div>

          <div class="overflow-x-auto rounded-4 border border-outline-gray-2">
            <table class="w-full min-w-[720px] border-collapse text-left">
              <thead class="bg-surface-gray-1 text-ink-gray-5">
                <tr>
                  <th class="w-10 border-b border-outline-gray-2 px-3 py-2">
                    <Checkbox
                      v-if="editable"
                      :model-value="allSelected"
                      :indeterminate="selected.size > 0 && !allSelected"
                      aria-label="Select all rows"
                      @update:model-value="toggleAll"
                    />
                  </th>
                  <th class="cell-head">Product</th>
                  <th class="cell-head w-40">SKU</th>
                  <th class="cell-head w-32 whitespace-nowrap text-right">Available Qty</th>
                  <th class="cell-head w-24 text-right">Qty</th>
                  <th class="cell-head w-32 text-right">Rate</th>
                  <th class="cell-head w-32 border-r-0 text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!form.items.length">
                  <td colspan="7" class="px-3 py-6 text-center text-ink-gray-5">No items yet</td>
                </tr>
                <tr
                  v-for="row in form.items"
                  :key="row._key"
                  class="border-b border-outline-gray-2 last:border-b-0"
                  :class="isShort(row) ? 'bg-surface-red-1' : 'hover:bg-surface-gray-1'"
                >
                  <td class="px-3 py-1.5">
                    <Checkbox
                      v-if="editable"
                      :model-value="selected.has(row._key)"
                      :aria-label="`Select row ${rowLabel(row)}`"
                      @update:model-value="toggleRow(row._key)"
                    />
                  </td>
                  <td class="cell py-1">
                    <LinkField
                      v-if="editable"
                      v-model="row.product"
                      class="w-full"
                      variant="ghost"
                      doctype="Platform Product"
                      :label-of="productLabel"
                      description-field="sku"
                      :fields="PRODUCT_FIELDS"
                      :search-fields="['product_title', 'variant_title', 'sku', 'barcode']"
                      :filters="{ status: 'Active' }"
                      :exclude="productNames"
                      :label="productLabel(row)"
                      placeholder="Select product"
                      @select="(p) => setProduct(row, p)"
                    />
                    <div v-else class="text-ink-gray-9">{{ productLabel(row) }}</div>
                    <div v-if="isShort(row)" class="mt-0.5 flex items-center gap-1 text-xs text-ink-red-6">
                      <span class="lucide-triangle-alert size-3" />
                      Exceeds stock — only {{ formatNumber(row.available_qty) }} available
                    </div>
                    <div v-if="editable && priceChange(row) != null" class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-ink-amber-7">
                      <span class="flex items-center gap-1">
                        <span class="lucide-tag size-3" />
                        Price changed: {{ formatCurrency(row.rate) }} → {{ formatCurrency(priceChange(row)) }}
                      </span>
                      <!-- For a rep the server sets a changed rate to the product's current price: the same thing. -->
                      <button type="button" class="font-medium text-ink-gray-9 underline underline-offset-2" @click="applyNewPrice(row)">
                        Apply new price
                      </button>
                    </div>
                  </td>
                  <td class="cell font-mono text-xs text-ink-gray-6">{{ row.sku || '—' }}</td>
                  <td class="cell text-right tabular-nums">
                    <Tooltip :text="tracks(row) ? null : 'Stock is not tracked for this product'" :disabled="tracks(row)">
                      <span :class="!tracks(row) && 'text-ink-gray-4'">{{ tracks(row) ? formatNumber(row.available_qty) : '—' }}</span>
                    </Tooltip>
                  </td>
                  <td class="cell p-0 text-right" :class="isShort(row) && 'outline outline-1 -outline-offset-1 outline-[--ink-red-5]'">
                    <input
                      v-if="editable"
                      v-model.number="row.qty"
                      type="number"
                      min="1"
                      class="num-input"
                      :class="isShort(row) && 'text-ink-red-6'"
                      :aria-label="`Quantity for ${rowLabel(row)}`"
                    />
                    <span v-else class="block px-3 tabular-nums" :class="isShort(row) && 'text-ink-red-6'">{{ formatNumber(row.qty, 3) }}</span>
                  </td>
                  <td class="cell p-0 text-right">
                    <!-- Only managers may set a rate other than the product's price (the server resets it otherwise). -->
                    <input
                      v-if="editable && session.isManager"
                      v-model.number="row.rate"
                      type="number"
                      min="0"
                      step="0.01"
                      class="num-input"
                      :aria-label="`Rate for ${rowLabel(row)}`"
                    />
                    <span v-else class="block px-3 tabular-nums">{{ formatCurrency(row.rate) }}</span>
                  </td>
                  <td class="cell border-r-0 text-right tabular-nums">{{ formatCurrency(amountOf(row)) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="editable" class="flex gap-2">
            <Button label="Add Row" icon-left="lucide-plus" @click="addRow()" />
            <Button label="Add Multiple" @click="pickerOpen = true" />
          </div>
        </div>

        <!-- Totals -->
        <div class="p-5">
          <h2 class="mb-3 text-base font-semibold text-ink-gray-9">Totals</h2>
          <div class="grid grid-cols-1 gap-6 sm:grid-cols-2">
            <Field label="Total Quantity">
              <div class="flex h-7 items-center rounded-4 bg-surface-gray-2 px-2 tabular-nums text-ink-gray-8">
                {{ formatNumber(totals.qty, 3) }}
              </div>
            </Field>
            <dl class="space-y-2.5">
              <div class="flex justify-between"><dt class="text-ink-gray-6">Net Total</dt><dd class="tabular-nums">{{ formatCurrency(totals.net) }}</dd></div>
              <div class="flex justify-between"><dt class="text-ink-gray-6">GST {{ formatNumber(form.tax_percent, 2) }}%</dt><dd class="tabular-nums">{{ formatCurrency(totals.tax) }}</dd></div>
              <div v-if="editable" class="flex items-start justify-between gap-3">
                <dt class="pt-1 text-ink-gray-6">Discount %</dt>
                <dd class="text-right">
                  <div class="flex items-center justify-end gap-3">
                    <TextInput
                      v-model="form.discount_percent"
                      type="number"
                      min="0"
                      max="100"
                      class="w-20 [&_input]:text-right"
                      aria-label="Discount %"
                    />
                    <span class="w-24 tabular-nums">{{ formatCurrency(totals.discount) }}</span>
                  </div>
                  <p class="mt-1 text-xs" :class="overDiscountLimit ? 'text-ink-red-5' : 'text-ink-gray-5'">{{ discountHint }}</p>
                </dd>
              </div>
              <div v-else class="flex justify-between">
                <dt class="text-ink-gray-6">Discount ({{ formatNumber(form.discount_percent || 0, 2) }}%)</dt>
                <dd class="tabular-nums">{{ formatCurrency(form.discount_amount || 0) }}</dd>
              </div>
              <div class="flex justify-between border-t border-outline-gray-2 pt-3 text-lg font-semibold text-ink-gray-9">
                <dt>Grand Total</dt><dd class="tabular-nums">{{ formatCurrency(totals.grand) }}</dd>
              </div>
            </dl>
          </div>
        </div>
      </div>

      <!-- Side column: store sync, payment, invoice -->
      <div class="space-y-4">
        <!-- Store sync -->
        <div class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-shopping-bag size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">{{ platform }} Sync</h2>
            <StatusPill :status="orderStatus(d)" />
          </div>
          <dl class="divide-y divide-outline-gray-1 px-4">
            <div v-for="item in syncRows" :key="item.label" class="flex items-center justify-between gap-3 py-2.5">
              <dt class="text-ink-gray-6">{{ item.label }}</dt>
              <dd class="truncate text-right" :class="item.muted ? 'text-ink-gray-5' : 'text-ink-gray-8'">
                <a v-if="item.href" :href="item.href" target="_blank" class="inline-flex items-center gap-1 text-ink-blue-6 hover:underline">
                  {{ item.value }}<span class="lucide-external-link size-3.5" />
                </a>
                <template v-else>{{ item.value }}</template>
              </dd>
            </div>
          </dl>
          <div class="space-y-2 p-4 pt-1">
            <p v-if="d.docstatus === 0" class="text-ink-gray-5">The order is sent to the store when it is submitted.</p>
            <div v-if="d.docstatus === 1 && d.sync_status === 'Failed'" class="flex gap-2 rounded-4 bg-surface-red-2 px-3 py-2.5 text-ink-red-6">
              <span class="lucide-circle-alert mt-px size-4 shrink-0" />
              <div class="min-w-0 break-words">
                <div class="font-semibold">Sync failed</div>
                <div v-if="d.sync_error">{{ d.sync_error }}</div>
              </div>
            </div>
            <Button
              v-if="d.docstatus === 1 && d.sync_status === 'Failed'"
              class="w-full"
              variant="solid"
              label="Retry Sync"
              icon-left="lucide-refresh-cw"
              :loading="retrying"
              @click="retrySync"
            />
            <Button
              v-if="session.isManager && d.docstatus !== 0"
              class="w-full"
              label="View Sync Log"
              :route="{ name: 'SyncLogs', query: { reference: name } }"
            />
          </div>
        </div>

        <!-- Payment (Razorpay payment link); read-only, written by the server -->
        <div v-if="d.docstatus === 1" class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-credit-card size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">Payment</h2>
            <StatusPill :status="pay.payment_status || 'Unpaid'" />
          </div>
          <dl class="divide-y divide-outline-gray-1 px-4">
            <div v-for="item in paymentRows" :key="item.label" class="flex items-center justify-between gap-3 py-2.5">
              <dt class="shrink-0 text-ink-gray-6">{{ item.label }}</dt>
              <dd class="min-w-0 truncate text-right" :class="item.muted ? 'text-ink-gray-5' : 'text-ink-gray-8'">
                <a v-if="item.href" :href="item.href" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-ink-blue-6 hover:underline">
                  {{ item.value }}<span class="lucide-external-link size-3.5" />
                </a>
                <template v-else>{{ item.value }}</template>
              </dd>
            </div>
          </dl>
          <div v-if="pay.razorpay_enabled && (pay.razorpay_payment_link_url || linkAction)" class="space-y-2 p-4 pt-1">
            <div v-if="pay.razorpay_payment_link_url" class="flex gap-2">
              <Button class="flex-1" label="Copy" icon-left="lucide-copy" @click="copyPaymentLink" />
              <Button v-if="linkActive" class="flex-1" label="WhatsApp" icon-left="lucide-message-circle" @click="shareOnWhatsApp" />
            </div>
            <div class="flex gap-2">
              <Button
                v-if="linkAction"
                class="flex-1"
                :variant="linkActive ? 'subtle' : 'solid'"
                :label="linkAction"
                icon-left="lucide-link"
                :loading="creatingLink"
                @click="onLinkAction"
              />
              <Button
                v-if="pay.razorpay_payment_link_id"
                class="flex-1"
                label="Refresh"
                icon-left="lucide-refresh-cw"
                :loading="refreshingPayment"
                @click="refreshPayment"
              />
            </div>
          </div>
          <div v-if="storeCheckout && !pay.razorpay_payment_link_url" class="space-y-2 p-4 pt-1">
            <Button
              class="flex-1"
              variant="solid"
              label="Pay in Store Checkout"
              icon-left="lucide-external-link"
              @click="openStoreCheckout"
            />
            <div class="flex gap-2">
              <Button class="flex-1" label="Copy Link" icon-left="lucide-copy" @click="copyStoreCheckout" />
              <Button class="flex-1" label="WhatsApp" icon-left="lucide-message-circle" @click="shareStoreCheckout" />
            </div>
          </div>
        </div>

        <!-- Invoice and confirmation email -->
        <div v-if="d.docstatus === 1" class="fs-card">
          <div class="flex items-center gap-2 border-b border-desk-border px-4 py-3">
            <span class="lucide-file-text size-4 text-ink-gray-7" />
            <h2 class="flex-1 text-base font-semibold text-ink-gray-9">Invoice</h2>
          </div>
          <dl class="divide-y divide-outline-gray-1 px-4">
            <div v-for="item in invoiceRows" :key="item.label" class="flex items-center justify-between gap-3 py-2.5">
              <dt class="shrink-0 text-ink-gray-6">{{ item.label }}</dt>
              <dd class="min-w-0 break-words text-right" :class="item.muted ? 'text-ink-gray-5' : 'text-ink-gray-8'">{{ item.value }}</dd>
            </div>
          </dl>
          <div class="space-y-2 p-4 pt-1">
            <div v-if="confirmationError" class="flex gap-2 rounded-4 bg-surface-red-2 px-3 py-2.5 text-ink-red-6">
              <span class="lucide-circle-alert mt-px size-4 shrink-0" />
              <div class="min-w-0 break-words">{{ d.confirmation_status }}</div>
            </div>
            <Button
              class="w-full"
              label="Download Invoice"
              icon-left="lucide-download"
              :href="invoiceUrl"
              target="_blank"
            />
            <Button
              v-if="d.external_order_id"
              class="w-full"
              label="Resend Email"
              icon-left="lucide-mail"
              :loading="sending"
              @click="resendConfirmation"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- Add Multiple -->
    <Dialog v-model="pickerOpen" title="Add products" size="xl" @after-leave="resetPicker">
      <div class="space-y-3">
        <TextInput v-model="pickerQuery" placeholder="Search by name, SKU or barcode" autofocus>
          <template #prefix><span class="lucide-search size-4 text-ink-gray-5" /></template>
        </TextInput>
        <div class="max-h-80 overflow-y-auto rounded-4 border border-outline-gray-2">
          <div v-if="picker.loading && !picker.data" class="flex justify-center py-8"><LoadingIndicator class="size-4" /></div>
          <div v-else-if="!pickerRows.length" class="py-8 text-center text-ink-gray-5">No products found</div>
          <label
            v-for="p in pickerRows"
            :key="p.name"
            class="flex cursor-pointer items-center gap-3 border-b border-outline-gray-1 px-3 py-2 last:border-b-0 hover:bg-surface-gray-1"
          >
            <Checkbox :model-value="picked.has(p.name)" @update:model-value="togglePicked(p.name)" />
            <span class="min-w-0 flex-1">
              <span class="block truncate text-ink-gray-9">{{ productLabel(p) }}</span>
              <span class="font-mono text-xs text-ink-gray-5">{{ p.sku || p.name }}</span>
            </span>
            <span class="text-right text-xs tabular-nums text-ink-gray-5">
              {{ formatCurrency(p.price) }}
              <span class="block">{{ stockText(p, 'in stock', 'Not tracked') }}</span>
            </span>
          </label>
        </div>
      </div>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Cancel" @click="pickerOpen = false" />
          <Button variant="solid" :label="picked.size ? `Add ${picked.size}` : 'Add'" :disabled="!picked.size" @click="addPicked" />
        </div>
      </template>
    </Dialog>

    <BarcodeScanner v-model="scanOpen" @scanned="onScanned" />

    <!-- Confirmations -->
    <Dialog v-model="confirm.open" :title="confirm.title" :message="confirm.message" :icon="confirm.icon" :theme="confirm.theme">
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button label="Not now" @click="confirm.open = false" />
          <Button variant="solid" :theme="confirm.theme" :label="confirm.label" :loading="confirm.busy" @click="runConfirm" />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { computed, defineComponent, h, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { RouterLink, onBeforeRouteLeave, useRouter } from 'vue-router'
import {
  Button,
  Checkbox,
  DatePicker,
  Dialog,
  Dropdown,
  LoadingIndicator,
  Select,
  TextInput,
  Tooltip,
  call,
  createDocumentResource,
  createResource,
  debounce,
  toast,
} from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import StatusPill from '@/components/StatusPill.vue'
import LinkField from '@/components/LinkField.vue'
import BarcodeScanner from '@/components/BarcodeScanner.vue'
import { isEmbedded } from '@/shopify/embedded'

const embedded = isEmbedded()
import { useSessionStore } from '@/stores/session'
import { useSellLocations } from '@/composables/useSellLocations'
import { onDocEvent, onDoctypeEvent } from '@/composables/useRealtime'
import { callMethod, findProductByBarcode } from '@/utils/api'
import { formatCurrency, formatDate, formatNumber, formatTime, orderStatus, relativeDay } from '@/utils/format'

const props = defineProps({ name: { type: String, required: true } })
const router = useRouter()
const session = useSessionStore()
const { options: locationOptions, multiple: multipleLocations, defaultLocation, locations: sellLocations } = useSellLocations()

/** Label above a form control, with the desk's red asterisk for required fields. */
const Field = defineComponent({
  props: { label: String, required: Boolean },
  setup: (p, { slots }) => () =>
    h('div', { class: 'space-y-1.5' }, [
      h('div', { class: 'text-sm text-ink-gray-6' }, [p.label, p.required ? h('span', { class: 'text-ink-red-5' }, ' *') : null]),
      slots.default?.(),
    ]),
})

/** A saved value in the same grey box as an input, optionally linking to its page. */
const ReadOnly = defineComponent({
  props: { value: String, aside: String, route: Object },
  setup: (p) => () =>
    h('div', { class: 'flex h-7 items-center gap-2 rounded-4 bg-surface-gray-2 px-2' }, [
      p.value && p.route
        ? h(RouterLink, { to: p.route, class: 'flex-1 truncate text-ink-gray-8 hover:underline' }, () => p.value)
        : h('span', { class: ['flex-1 truncate', p.value ? 'text-ink-gray-8' : 'text-ink-gray-4'] }, p.value || '—'),
      p.aside ? h('span', { class: 'shrink-0 text-ink-gray-5' }, p.aside) : null,
    ]),
})

// Name of the order's location: saved with the order, else looked up from the shop's locations.
const locationName = computed(() => {
  const id = form.value?.location_id
  return form.value?.location_name || sellLocations.value.find((l) => l.location_id === id)?.location_name || id || null
})

const PRODUCT_FIELDS = ['product_title', 'variant_title', 'sku', 'available_qty', 'price', 'track_inventory', 'external_variant_id']

// ---------------------------------------------------------------- document

const doc = createDocumentResource({
  doctype: 'Field Order',
  name: props.name,
  auto: true,
  onSuccess: () => resetForm(),
})
const d = computed(() => doc.doc || {})
const errorText = computed(() => String(doc.get.error?.messages?.[0] || doc.get.error || ''))
const editable = computed(() => d.value.docstatus === 0)

// The editable copy. `_key` identifies rows in the template, including unsaved ones.
const form = ref(null)
let keySeq = 0
const withKey = (row) => ({ ...row, _key: row.name || `new-${++keySeq}` })

function resetForm() {
  form.value = { ...doc.doc, items: (doc.doc.items || []).map(withKey) }
  selected.value = new Set()
}

// Compares only what the user can change, so loading or recomputed fields never count as edits.
const editableState = (o) =>
  o && JSON.stringify([o.retailer, o.visit || null, o.order_date, (o.items || []).map((r) => [r.product || null, +r.qty || 0, +r.rate || 0]), +o.discount_percent || 0])
const dirty = computed(() => !!form.value && editable.value && editableState(form.value) !== editableState(doc.doc))

onBeforeRouteLeave(() => !dirty.value || window.confirm('You have unsaved changes to this order. Leave without saving?'))

// The city shown beside the retailer on a submitted order.
const retailerCity = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({ doctype: 'Retailer', filters: form.value.retailer, fieldname: 'city' }),
})
watch(
  () => !editable.value && form.value?.retailer,
  (retailer) => retailer && retailerCity.fetch(),
)

function onRetailer(retailer) {
  form.value.retailer_name = retailer.retailer_name
  form.value.visit = null
}

// ---------------------------------------------------------------- items and stock

// track_inventory is on the product, not the row: fetched for every product on the order.
const tracked = ref({})
const productNames = computed(() => (form.value?.items || []).map((r) => r.product).filter(Boolean))
const trackedLookup = createResource({
  url: 'frappe.client.get_list',
  makeParams: (names) => ({
    doctype: 'Platform Product',
    filters: { name: ['in', names] },
    fields: ['name', 'track_inventory'],
    limit_page_length: 0,
  }),
  onSuccess: (rows) => rows.forEach((p) => (tracked.value[p.name] = !!p.track_inventory)),
})
watch(
  productNames,
  (names) => {
    const missing = names.filter((n) => !(n in tracked.value))
    if (missing.length) trackedLookup.submit(missing)
  },
  { immediate: true },
)

const tracks = (row) => !!tracked.value[row.product]
const isShort = (row) => tracks(row) && (+row.qty || 0) > (+row.available_qty || 0)
const amountOf = (row) => Math.round((+row.qty || 0) * (+row.rate || 0) * 100) / 100
const warningRows = computed(() => (form.value?.items || []).filter(isShort))

const productLabel = (p) => [p.product_title || p.product, p.variant_title].filter(Boolean).join(' · ')
const rowLabel = (row) => productLabel(row) || `row ${form.value.items.indexOf(row) + 1}`

// Saved totals come from the server. While editing, they are estimated the way
// FieldOrder._compute_totals works; the server's rounding can differ by a paisa.
const totals = computed(() => {
  if (!dirty.value) {
    const o = d.value
    return { qty: o.total_qty, net: o.net_total, tax: o.tax_amount, discount: o.discount_amount, grand: o.grand_total }
  }
  const items = form.value.items
  const net = Math.round(items.reduce((sum, r) => sum + amountOf(r), 0) * 100) / 100
  const tax = Math.round(((net * (+form.value.tax_percent || 0)) / 100) * 100) / 100
  const discount = Math.round(((net * (+form.value.discount_percent || 0)) / 100) * 100) / 100
  return {
    qty: items.reduce((sum, r) => sum + (+r.qty || 0), 0),
    net,
    tax,
    discount,
    grand: Math.round((net + tax - discount) * 100) / 100,
  }
})

// The rep's discount limit (managers have none); the server enforces it on save.
const repLimit = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({ doctype: 'Sales Rep', filters: form.value.sales_rep, fieldname: 'max_discount_pct' }),
})
watch(
  () => !session.isManager && editable.value && form.value?.sales_rep,
  (rep) => rep && repLimit.fetch(),
  { immediate: true },
)
const maxDiscount = computed(() => (session.isManager ? null : +repLimit.data?.max_discount_pct || 0))
const overDiscountLimit = computed(
  () => maxDiscount.value !== null && repLimit.data && (+form.value.discount_percent || 0) > maxDiscount.value,
)
const discountHint = computed(() => {
  if (maxDiscount.value === null) return 'no limit'
  if (!repLimit.data) return ''
  return maxDiscount.value > 0 ? `max ${formatNumber(maxDiscount.value, 2)}%` : 'no discount allowed'
})

// Stock of listed products (search results, Add Multiple) at the ORDER's location: Platform
// Product.available_qty is the default location's figure only. Keyed `${location}|${product}`.
const listedStock = ref({})
const stockKey = (product) => `${form.value?.location_id}|${product}`
const atDefaultLocation = computed(
  () => !form.value?.location_id || form.value.location_id === defaultLocation.value?.location_id,
)
// Quantity at the order's location; null while it is still loading.
function listedQty(p) {
  const key = stockKey(p.name)
  if (key in listedStock.value) return listedStock.value[key]
  return atDefaultLocation.value ? p.available_qty : null
}
const stockText = (p, suffix, untracked) =>
  !p.track_inventory ? untracked : listedQty(p) == null ? '…' : `${formatNumber(listedQty(p))} ${suffix}`

async function loadListedStock(location, products) {
  try {
    const rows = await call('frappe.client.get_list', {
      doctype: 'Product Stock',
      filters: { location_id: location, product: ['in', products] },
      fields: ['product', 'available_qty'],
      limit_page_length: 0,
    })
    // No row means no known stock there.
    const found = Object.fromEntries(products.map((name) => [`${location}|${name}`, 0]))
    rows.forEach((r) => (found[`${location}|${r.product}`] = r.available_qty))
    listedStock.value = { ...listedStock.value, ...found }
  } catch {
    // The list falls back to the default location's figure or "…".
  }
}
function setProduct(row, p) {
  tracked.value[p.name] = !!p.track_inventory
  Object.assign(row, {
    product: p.name,
    product_title: p.product_title,
    variant_title: p.variant_title,
    sku: p.sku,
    available_qty: listedQty(p) ?? 0,
    rate: p.price,
    external_variant_id: p.external_variant_id,
  })
  if (p.track_inventory) loadLiveStock(row, p.name)
}

// The picker shows stock from the last sync; replace it with the store's current
// figure at the order's location as soon as a tracked product is added. On failure the synced figure stays,
// and submitting checks live stock again anyway.
async function loadLiveStock(row, product) {
  try {
    const location = form.value.location_id
    const live = await call('field_sales_woocommerce.api.get_live_stock', { products: [product], location_id: location })
    if (product in live) listedStock.value = { ...listedStock.value, [`${location}|${product}`]: live[product] }
    // The row may have been switched to another product meanwhile.
    if (row.product === product && product in live && form.value.location_id === location) row.available_qty = live[product]
  } catch {
    // Keep the synced figure.
  }
}

function addRow(product) {
  form.value.items.push(withKey({ doctype: 'Field Order Item', product: null, qty: 1, rate: 0 }))
  // Fill the reactive row (not the plain object), so the live stock that arrives later shows.
  if (product) setProduct(form.value.items[form.value.items.length - 1], product)
}

// Search-to-add in the items card: same catalog search as the picker, but the
// picked product goes straight onto the order.
const quickSearchEl = ref(null)
const quickQuery = ref('')
const quickOpen = ref(false)
const quickActive = ref(null)
const quickSearch = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => {
    const txt = quickQuery.value.trim()
    return {
      doctype: 'Platform Product',
      fields: ['name', ...PRODUCT_FIELDS],
      filters: { status: 'Active' },
      or_filters: txt
        ? ['product_title', 'variant_title', 'sku', 'barcode'].map((f) => [f, 'like', `%${txt}%`])
        : null,
      order_by: 'product_title asc',
      limit_page_length: 10,
    }
  },
})
const quickRows = computed(() => (quickSearch.data || []).filter((p) => !productNames.value.includes(p.name)))
watch(quickQuery, debounce(() => {
  quickOpen.value = true
  quickSearch.fetch()
}, 250))
function quickAdd(p) {
  addRow(p)
  quickQuery.value = ''
  quickOpen.value = false
  quickActive.value = null
  quickSearch.setData(null)
  nextTick(() => quickSearchEl.value?.$el?.querySelector('input')?.focus())
}

// Camera scan: the scanned product goes onto the order with qty 1, or +1 if it is already there.
const scanOpen = ref(false)
async function onScanned(code) {
  let product
  try {
    product = await findProductByBarcode(code, { fields: ['name', ...PRODUCT_FIELDS], filters: { status: 'Active' } })
  } catch {
    toast.error('Could not look up the barcode. Try again.')
    return
  }
  if (!product) {
    toast.warning(`No product with barcode ${code}`)
    return
  }
  const row = form.value.items.find((r) => r.product === product.name)
  if (row) {
    row.qty = (+row.qty || 0) + 1
    toast.success(`${productLabel(product)} — qty ${row.qty}`)
  } else {
    // An empty row left by Add Row is filled first rather than left dangling.
    form.value.items = form.value.items.filter((r) => r.product)
    addRow(product)
    toast.success(`Added ${productLabel(product)}`)
  }
}

const selected = ref(new Set())
const allSelected = computed(() => !!form.value?.items.length && selected.value.size === form.value.items.length)
function toggleRow(key) {
  const next = new Set(selected.value)
  next.has(key) ? next.delete(key) : next.add(key)
  selected.value = next
}
function toggleAll(on) {
  selected.value = new Set(on ? form.value.items.map((r) => r._key) : [])
}
function deleteSelected() {
  form.value.items = form.value.items.filter((r) => !selected.value.has(r._key))
  selected.value = new Set()
}

// ---------------------------------------------------------------- Add Multiple

const pickerOpen = ref(false)
const pickerQuery = ref('')
const picked = ref(new Set())
const picker = createResource({
  url: 'frappe.client.get_list',
  makeParams: () => {
    const txt = pickerQuery.value.trim()
    return {
      doctype: 'Platform Product',
      fields: ['name', ...PRODUCT_FIELDS],
      filters: { status: 'Active' },
      or_filters: txt ? ['product_title', 'variant_title', 'sku', 'barcode'].map((f) => [f, 'like', `%${txt}%`]) : null,
      order_by: 'product_title asc',
      limit_page_length: 50,
    }
  },
})
const pickerRows = computed(() => (picker.data || []).filter((p) => !productNames.value.includes(p.name)))
watch(pickerOpen, (open) => open && picker.reload())
watch(pickerQuery, debounce(() => picker.reload(), 250))

function togglePicked(name) {
  const next = new Set(picked.value)
  next.has(name) ? next.delete(name) : next.add(name)
  picked.value = next
}
function addPicked() {
  // An empty row left by Add Row is filled first rather than left dangling.
  form.value.items = form.value.items.filter((r) => r.product)
  ;(picker.data || []).filter((p) => picked.value.has(p.name)).forEach((p) => addRow(p))
  pickerOpen.value = false
}
function resetPicker() {
  pickerQuery.value = ''
  picked.value = new Set()
}

watch(
  [() => quickRows.value, () => pickerRows.value, () => form.value?.location_id],
  ([quick, pick, location]) => {
    if (!location) return
    const missing = [...new Set([...quick, ...pick].map((p) => p.name))].filter((n) => !(`${location}|${n}` in listedStock.value))
    if (missing.length) loadListedStock(location, missing)
  },
)

// ---------------------------------------------------------------- live price and stock

// Store webhooks announce price and stock changes (sync/webhooks.py). Products not on the
// order show the new figures in the search results and Add Multiple; rows already on a
// draft keep their rate and only offer the new price. Submitted orders are left alone.
const newPrices = ref({}) // product -> latest store price announced while the page is open
const priceChange = (row) => {
  const price = row.product ? newPrices.value[row.product] : null
  return price != null && +price !== +row.rate ? price : null
}
function applyNewPrice(row) {
  row.rate = newPrices.value[row.product]
}
watch(() => props.name, () => (newPrices.value = {}))

// Update listed products (search results, Add Multiple) in place.
function patchListed(products, values) {
  for (const resource of [quickSearch, picker]) {
    if (resource.data?.some((p) => products.includes(p.name))) {
      resource.setData(resource.data.map((p) => (products.includes(p.name) ? { ...p, ...values } : p)))
    }
  }
}

// Razorpay webhook / refresh changed the payment fields.
onDocEvent('Field Order', props.name, 'field_order_payment_updated', () => reloadPayment())

onDoctypeEvent('Platform Product', 'product_price_updated', ({ product, price, compare_at_price }) => {
  patchListed([product], { price, compare_at_price })
  if (editable.value && productNames.value.includes(product)) newPrices.value = { ...newPrices.value, [product]: price }
})

onDoctypeEvent('Platform Product', 'stock_updated', ({ products, available_qty, location_id }) => {
  listedStock.value = { ...listedStock.value, ...Object.fromEntries(products.map((n) => [`${location_id}|${n}`, available_qty])) }
  // Platform Product.available_qty is the default location's figure.
  if (location_id === defaultLocation.value?.location_id) patchListed(products, { available_qty })
  const orderLocation = form.value?.location_id || defaultLocation.value?.location_id
  if (editable.value && location_id === orderLocation) {
    form.value.items.filter((r) => products.includes(r.product)).forEach((r) => (r.available_qty = available_qty))
  }
})

// Switching a draft to another location: save the order there, then re-check its rows live
// at the new location (the server also swaps the rows' stock for the stored figures on save).
const locationBusy = ref(false)
async function changeLocation(location) {
  if (!location || location === d.value.location_id) return
  locationBusy.value = true
  try {
    if (dirty.value) {
      form.value.location_id = location
      if (!(await save())) {
        form.value.location_id = d.value.location_id
        return
      }
    } else {
      await callMethod('frappe.client.set_value', {
        doctype: 'Field Order',
        name: props.name,
        fieldname: 'location_id',
        value: location,
      })
    }
    if (d.value.items?.some((r) => r.product) || form.value.items.some((r) => r.product)) {
      await callMethod('field_sales_woocommerce.api.refresh_stock', { field_order: props.name })
    }
    await doc.reload()
    toast.success(`Stock checked at ${locationName.value}`)
  } catch {
    await doc.reload() // callMethod already showed the error; put the saved location back
  } finally {
    locationBusy.value = false
  }
}

// ---------------------------------------------------------------- actions

const saving = ref(false)

function validateForm() {
  const f = form.value
  const problem =
    (!f.retailer && 'Select a customer') ||
    (!f.order_date && 'Set the order date') ||
    (!f.items.length && 'Add at least one item') ||
    (f.items.some((r) => !r.product) && 'Select a product in every row, or delete the empty rows') ||
    (f.items.some((r) => !(+r.qty > 0)) && 'Every quantity must be greater than 0') ||
    (!(+f.discount_percent >= 0 && +f.discount_percent <= 100) && 'Discount must be between 0 and 100%') ||
    (overDiscountLimit.value && `Discount is over your limit (${discountHint.value})`)
  if (problem) toast.error(problem)
  return !problem
}

async function save() {
  if (!validateForm()) return false
  saving.value = true
  try {
    const items = form.value.items.map(({ _key, ...row }) => row)
    await callMethod('frappe.client.save', { doc: { ...form.value, items, discount_percent: +form.value.discount_percent || 0 } })
    await doc.reload()
    toast.success('Order saved')
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

const confirm = reactive({ open: false, title: '', message: '', label: '', icon: null, theme: 'gray', busy: false, action: null })
function ask(options) {
  Object.assign(confirm, { icon: null, theme: 'gray', ...options, open: true, busy: false })
}
async function runConfirm() {
  confirm.busy = true
  try {
    await confirm.action()
    confirm.open = false
  } catch {
    // callMethod already showed the error
  } finally {
    confirm.busy = false
  }
}

async function confirmSubmit() {
  // Check the store's current stock first (as the desk does), so a shortage shows on
  // the rows before confirming. The server checks it again when the order is submitted.
  saving.value = true
  try {
    await callMethod('field_sales_woocommerce.api.refresh_stock', { field_order: props.name })
    await doc.reload()
  } catch {
    return // callMethod already showed the error (e.g. the store could not be reached)
  } finally {
    saving.value = false
  }
  if (warningRows.value.length) {
    toast.error('Not enough stock: reduce the highlighted quantities before submitting')
    return
  }
  ask({
    title: 'Submit order?',
    message: `${props.name} will be sent to the store for ${formatCurrency(totals.value.grand)}. A submitted order can no longer be edited.`,
    label: 'Submit',
    action: async () => {
      await callMethod('frappe.client.submit', { doc: doc.doc })
      await doc.reload()
      toast.success('Order submitted; syncing to the store')
    },
  })
}

async function refreshStock() {
  if (dirty.value && !(await save())) return
  await callMethod('field_sales_woocommerce.api.refresh_stock', { field_order: props.name })
  await doc.reload()
  toast.success('Stock updated from the store')
}

const moreActions = computed(() => {
  const actions = []
  if (editable.value) {
    actions.push({ label: 'Refresh Stock', icon: 'lucide-refresh-ccw', onClick: refreshStock })
  }
  if (storeUrl.value) {
    actions.push({ label: `Open in ${platform.value}`, icon: 'lucide-external-link', onClick: () => window.open(storeUrl.value, '_blank') })
  }
  if (d.value.docstatus === 1) {
    actions.push({
      label: 'Cancel Order',
      icon: 'lucide-circle-x',
      theme: 'red',
      onClick: () =>
        ask({
          title: 'Cancel order?',
          message: `${props.name} stays in Field Sales as cancelled. An order already created in the store is not cancelled there.`,
          label: 'Cancel Order',
          icon: 'lucide-circle-x',
          theme: 'red',
          action: async () => {
            await callMethod('frappe.client.cancel', { doctype: 'Field Order', name: props.name })
            await doc.reload()
            toast.success('Order cancelled')
          },
        }),
    })
  }
  if (d.value.docstatus !== 1) {
    actions.push({
      label: 'Delete',
      icon: 'lucide-trash-2',
      theme: 'red',
      onClick: () =>
        ask({
          title: 'Delete order?',
          message: `${props.name} will be deleted permanently.`,
          label: 'Delete',
          icon: 'lucide-trash-2',
          theme: 'red',
          action: async () => {
            await callMethod('frappe.client.delete', { doctype: 'Field Order', name: props.name })
            form.value = null // nothing left to save; skip the unsaved-changes prompt
            toast.success('Order deleted')
            router.push({ name: 'Orders' })
          },
        }),
    })
  }
  return actions
})

// ---------------------------------------------------------------- store sync

const syncInfo = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_order_sync_info',
  params: { field_order: props.name },
  auto: true,
})
// Refetch once the sync writes new IDs, so the store link appears.
watch(
  () => [d.value.external_order_id, d.value.external_draft_id],
  (now, before) => before && now.join() !== before.join() && syncInfo.reload(),
)

const platform = computed(() => d.value.platform || session.store.platform || 'Store')
const storeUrl = computed(() => syncInfo.data?.store_url)

const syncRows = computed(() => {
  const o = d.value
  const attempts = o.sync_attempts || 0
  const max = syncInfo.data?.max_attempts
  // Order: the store's order number with its id, e.g. "#1013 (6186254401676)".
  const orderValue = !o.external_order_id
    ? 'Not created'
    : o.store_order_name
      ? `${o.store_order_name} (${o.external_order_id})`
      : `#${o.external_order_id}`
  // The draft row only matters in Draft mode or when a draft exists.
  const showDraft = !!o.external_draft_id || syncInfo.data?.order_mode === 'Draft'
  return [
    ...(showDraft
      ? [
          {
            label: 'Draft Order',
            value: !o.external_draft_id
              ? 'Not created'
              : o.store_draft_name
                ? `${o.store_draft_name} (${o.external_draft_id})`
                : `#${o.external_draft_id}`,
            muted: !o.external_draft_id,
            href: syncInfo.data?.draft_url || null,
          },
        ]
      : []),
    {
      label: 'Order',
      value: orderValue,
      muted: !o.external_order_id,
      href: syncInfo.data?.order_url || null,
    },
    { label: 'Location', value: syncInfo.data?.location_name || '—', muted: !syncInfo.data?.location_name },
    {
      label: 'Last Attempt',
      value: o.last_sync_attempt ? `${relativeDay(o.last_sync_attempt)}, ${formatTime(o.last_sync_attempt)}` : 'Never',
      muted: !o.last_sync_attempt,
    },
    { label: 'Attempts', value: max ? `${attempts} of ${max}` : attempts },
  ]
})

const retrying = ref(false)
async function retrySync() {
  retrying.value = true
  try {
    const r = await callMethod('field_sales_woocommerce.api.retry_sync', { field_order: props.name })
    toast.success(r.message)
    setTimeout(() => doc.reload(), 4000)
  } finally {
    retrying.value = false
  }
}
// ---------------------------------------------------------------- invoice

const invoiceUrl = computed(
  () => `/api/method/frappe.utils.print_format.download_pdf?doctype=Field%20Order&name=${encodeURIComponent(props.name)}&format=Field%20Sales%20Invoice`,
)
const confirmationError = computed(() => (d.value.confirmation_status || '').startsWith('Error'))

// ---------------------------------------------------------------- payment

// Payment fields come from their own call, so the card follows the server (webhook, refresh)
// without reloading the whole order.
const paymentInfo = createResource({
  url: 'field_sales_woocommerce.frontend_api.get_order_payment_info',
  method: 'GET',
  makeParams: () => ({ field_order: props.name }),
})
watch(
  () => d.value.docstatus === 1 && props.name,
  (name) => name && paymentInfo.fetch(),
  { immediate: true },
)
const pay = computed(() => paymentInfo.data || {})
const creatingLink = ref(false)
const refreshingPayment = ref(false)

const paymentRows = computed(() => {
  const o = pay.value
  const when = (v) => (v ? `${relativeDay(v)}, ${formatTime(v)}` : null)
  const rows = [
    { label: 'Amount Paid', value: `${formatCurrency(o.amount_paid || 0)} of ${formatCurrency(o.grand_total ?? d.value.grand_total ?? 0)}`, muted: !o.amount_paid },
  ]
  if (o.razorpay_payment_link_url) rows.push({ label: 'Payment Link', value: o.razorpay_payment_link_id || 'Open', href: o.razorpay_payment_link_url })
  else if (storeCheckout.value) rows.push({ label: 'Store Checkout', value: 'Pay online', href: o.store_checkout_url })
  else rows.push({ label: 'Payment Link', value: 'Not created', muted: true })
  if (o.payment_link_expires_on && !o.paid_on) rows.push({ label: 'Link Expires', value: when(o.payment_link_expires_on) })
  if (o.paid_on) rows.push({ label: 'Paid On', value: when(o.paid_on) })
  if (o.razorpay_payment_id) rows.push({ label: 'Payment ID', value: o.razorpay_payment_id })
  if (o.store_marked_paid_on) rows.push({ label: 'Store', value: `Marked paid ${when(o.store_marked_paid_on)}` })
  return rows
})

const linkExpired = computed(() => {
  const exp = pay.value.payment_link_expires_on
  return !!exp && new Date(exp.replace(' ', 'T')) <= new Date()
})
// A link the retailer can still pay.
const linkActive = computed(
  () => !!pay.value.razorpay_payment_link_url && pay.value.payment_status === 'Link Sent' && !linkExpired.value,
)
// Button label, or null when no link should be made. Partially Paid is left out:
// a new link would ask for the full total again.
const linkAction = computed(() => {
  const o = pay.value
  if (!o.razorpay_enabled || !o.can_create_link || o.payment_links_available === false || !(o.grand_total > 0)) return null
  if (o.payment_status === 'Paid' || o.payment_status === 'Partially Paid') return null
  return linkActive.value ? 'New Link' : 'Create Link'
})

// The store's own checkout link: payment without leaving Shopify (the App Store
// edition's only payment link; direct installs get it when Razorpay has no link yet).
const storeCheckout = computed(() => {
  const o = pay.value
  return !!o.store_checkout_url && !['Paid', 'Partially Paid'].includes(o.payment_status)
})

function openStoreCheckout() {
  window.open(pay.value.store_checkout_url, '_blank', 'noopener')
}

async function copyStoreCheckout() {
  try {
    await navigator.clipboard.writeText(pay.value.store_checkout_url || '')
    toast.success('Checkout link copied')
  } catch {
    toast.error('Could not copy to clipboard')
  }
}

function shareStoreCheckout() {
  const o = pay.value
  const text = `Hello ${o.retailer_name || d.value.retailer_name || ''}, please pay ${formatCurrency(o.grand_total)} for order ${o.order_label} in the store checkout: ${o.store_checkout_url}`
  if (!o.whatsapp_number) toast.warning('The retailer has no valid mobile number; pick the contact in WhatsApp.')
  window.open(`https://wa.me/${o.whatsapp_number || ''}?text=${encodeURIComponent(text)}`, '_blank', 'noopener')
}

async function reloadPayment() {
  await Promise.all([paymentInfo.reload(), doc.reload()])
}

function onLinkAction() {
  if (!linkActive.value) return createPaymentLink(false)
  ask({
    title: 'Replace payment link?',
    message: 'The current link will be cancelled on Razorpay and stop working. A new link is created for the same amount.',
    label: 'New Link',
    action: () => createPaymentLink(true),
  })
}

async function createPaymentLink(replace) {
  creatingLink.value = true
  try {
    await callMethod('field_sales_woocommerce.payments.razorpay.create_link_for_order', { field_order: props.name, replace: replace ? 1 : 0 })
    toast.success('Payment link created')
  } finally {
    // Reload either way: a failed retry can still mark the old link cancelled.
    await reloadPayment()
    creatingLink.value = false
  }
}

async function refreshPayment() {
  refreshingPayment.value = true
  try {
    const r = await callMethod('field_sales_woocommerce.payments.razorpay.refresh_payment_status', { field_order: props.name })
    toast.info(`Payment status: ${r.payment_status}`)
    await reloadPayment()
  } finally {
    refreshingPayment.value = false
  }
}

async function copyPaymentLink() {
  try {
    await navigator.clipboard.writeText(pay.value.razorpay_payment_link_url || '')
    toast.success('Payment link copied')
  } catch {
    toast.error('Could not copy to clipboard')
  }
}

function shareOnWhatsApp() {
  const o = pay.value
  const text = `Hello ${o.retailer_name || d.value.retailer_name || ''}, please pay ${formatCurrency(o.grand_total)} for order ${o.order_label} using this link: ${o.razorpay_payment_link_url}`
  if (!o.whatsapp_number) toast.warning('The retailer has no valid mobile number; pick the contact in WhatsApp.')
  window.open(`https://wa.me/${o.whatsapp_number || ''}?text=${encodeURIComponent(text)}`, '_blank', 'noopener')
}

const invoiceRows = computed(() => {
  const o = d.value
  const sent = o.confirmation_sent_on ? `${relativeDay(o.confirmation_sent_on)}, ${formatTime(o.confirmation_sent_on)}` : null
  const status = o.confirmation_status || ''
  return [
    { label: 'Invoice No', value: o.invoice_no || 'Not issued', muted: !o.invoice_no },
    { label: 'Store Order No', value: o.store_order_name || '—', muted: !o.store_order_name },
    {
      label: 'Email',
      // Sent: when and to whom; skipped: why; errors show in the box below.
      value: status.startsWith('Sending') ? 'Sending…' : sent ? `${status || 'Sent'} · ${sent}` : confirmationError.value ? 'Failed' : status || 'Not sent yet',
      muted: !sent && !confirmationError.value && !status.startsWith('Skipped'),
    },
  ]
})

const resending = ref(false)
const sending = computed(() => resending.value || (d.value.confirmation_status || '').startsWith('Sending'))
let pollTimer = null
function stopPolling() {
  clearTimeout(pollTimer)
  pollTimer = null
}
onBeforeUnmount(stopPolling)
// An order opened while its email is still being sent: follow it too.
watch(
  () => (d.value.confirmation_status || '').startsWith('Sending'),
  (busy) => busy && !pollTimer && pollConfirmation(),
  { immediate: true },
)

/** Reload the order every 3 s (for up to 2 min) until the background job has written its result. */
function pollConfirmation() {
  stopPolling()
  const until = Date.now() + 120000
  const tick = async () => {
    try {
      await doc.reload()
    } catch {
      // keep trying until the time is up
    }
    const status = d.value.confirmation_status || ''
    if (!status.startsWith('Sending')) {
      resending.value = false
      if (status.endsWith('without payment link')) toast.warning(`${status}. Create a link from the Payment card and share it.`)
      else if (status.startsWith('Sent to')) toast.success(status)
      else if (status.startsWith('Skipped')) toast.info(status)
      else toast.error(status || 'Email was not sent')
      return stopPolling()
    }
    if (Date.now() >= until) {
      resending.value = false
      toast.info('Still sending — check again later')
      return stopPolling()
    }
    pollTimer = setTimeout(tick, 3000)
  }
  pollTimer = setTimeout(tick, 3000)
}

async function resendConfirmation() {
  resending.value = true
  try {
    await callMethod('field_sales_woocommerce.sync.confirmation.resend_order_confirmation', { name: props.name })
    toast.info('Sending email…')
    await doc.reload()
    pollConfirmation()
  } catch (e) {
    resending.value = false
    throw e
  }
}
</script>

<style scoped>
.cell-head {
  @apply border-b border-r border-outline-gray-2 px-3 py-2 font-normal;
}
.cell {
  @apply border-r border-outline-gray-2 px-3 py-1.5;
}
.num-input {
  @apply h-9 w-full border-0 bg-transparent px-3 text-right tabular-nums focus:bg-surface-base focus:outline-none;
  appearance: textfield;
}
.num-input:focus {
  box-shadow: inset 0 0 0 1px var(--outline-gray-4);
}
.num-input::-webkit-inner-spin-button,
.num-input::-webkit-outer-spin-button {
  appearance: none;
}
</style>
