# Razorpay payments (WooCommerce edition)

Two ways to collect a payment, one payment history, one set of Razorpay keys (kept in Agamsale).

| | Workflow A: payment link | Workflow B: store checkout |
|---|---|---|
| Started by | A rep in the app (mobile or web) on a submitted Field Order | The customer on the WooCommerce checkout, or from the order's pay page (the app's *Pay in Store Checkout*) |
| Razorpay object | Payment Link (`/payment_links`) | Order (`/orders`) + Razorpay Checkout |
| Confirmed by | Razorpay webhook `payment_link.*`, hourly reconcile, *Refresh* | Signature check + payment fetch on return; webhook `payment.captured` / `order.paid` if the customer never returns |
| Store order | Marked paid by Agamsale (REST `set_paid`, payment method `agamsale_razorpay`) | Marked paid by WordPress (`payment_complete`), or by Agamsale via REST from the webhook |
| Refund | From the WooCommerce order (gateway `process_refund`) or the Razorpay Dashboard | Same |

## Architecture

```
 Mobile / web app ──(Frappe session/API key)──▶ Frappe (Agamsale)
                                                  │  payments/razorpay.py   links, webhook, reconcile
                                                  │  payments/checkout.py   checkout API, checkout/refund webhooks
                                                  │  payments/transactions.py  Payment Transaction history
                                                  │  connectors/woocommerce.py  REST: read order, set_paid, notes
                                                  ▼
 Razorpay ◀───────── basic auth (Key ID/Secret, server only) ─────────┘
    │  webhook (HMAC with webhook secret) ──▶ /api/method/field_sales_woocommerce.payments.razorpay.webhook
    ▼
 Customer browser ── Razorpay Checkout (card/UPI details go to Razorpay only)
    │
 WordPress (plugin "Agamsale" 1.3.0)
    includes/class-wc-gateway-agamsale-razorpay.php   gateway, pay page, callback, refunds
    includes/class-agamsale-api.php                   signed calls to Frappe
    includes/class-agamsale-razorpay-blocks.php       Checkout block registration
```

**Trust boundaries.** Razorpay secrets live only in Frappe (`Razorpay Settings`, encrypted
password fields). WordPress calls Frappe with requests signed by the plugin secret it already
shares for wp-admin sign-in (`Store Connection → WordPress Plugin Secret`). Neither the mobile
app, the browser nor WordPress ever receives a Razorpay or Frappe secret; the browser gets the
public Key ID only.

**Never paid until verified.** A Field Order or WooCommerce order becomes Paid only after
Agamsale has: (1) checked Razorpay's signature (checkout) or the webhook HMAC (links/webhooks),
(2) fetched or received the payment and matched its Razorpay order, amount (paise) and currency
to the order, and (3) seen it `captured` (an `authorized` checkout payment is captured first).
Amounts are always read from WooCommerce/Frappe, never from the browser.

## Data model

**New DocType `Payment Transaction`** (read-only; System Manager and Sales Manager read; reps see
their orders' rows through the APIs). One row per Razorpay object or manual payment, unique on
`transaction_key` (`link:<plink>`, `rzorder:<order>`, `payment:<pay>`, `refund:<rfnd>`,
`refundreq:<request id>`, `manual:<Field Order>`), so repeats update the row. A row in
Captured / Paid / Processed never goes back.

| Field | Notes |
|---|---|
| transaction_type | Payment Link, Checkout Order, Payment, Refund |
| status | Created, Pending, Authorized, Captured, Partially Paid, Paid, Failed, Expired, Cancelled, Processed |
| source | Payment Link, Store Checkout, Manual |
| amount, currency, occurred_on, method, reference, error | |
| reference_order | Field Order (when the store order came from Agamsale) |
| store_order_id | WooCommerce order id |
| razorpay_payment_link_id, razorpay_order_id, razorpay_payment_id, razorpay_refund_id | |

**Field Order:** new `amount_refunded` (Currency, server-written); `payment_status` gains
*Partially Refunded* and *Refunded*. **Sync Log:** operations *Checkout Order*, *Create Checkout
Order*, *Capture Payment*, *Refund Payment*. Run `bench migrate` (no data patch needed).

## APIs

### For the apps (Frappe session or API key)

| Method | Change |
|---|---|
| `GET field_sales_woocommerce.frontend_api.get_order_payment_info?field_order=` | Adds `amount_refunded`, `transactions` (history rows, newest first: `name, transaction_type, status, source, amount, currency, occurred_on, method, reference, razorpay_payment_link_id, razorpay_order_id, razorpay_payment_id, razorpay_refund_id, error`). `can_mark_paid` is false for refunded orders. |
| `GET field_sales_woocommerce.api.mobile.get_order_detail` | Adds `razorpay_payment_id`, `amount_paid`, `amount_refunded`. |
| `POST ...payments.razorpay.create_link_for_order` (`field_order`, `replace`) | Unchanged contract; replacing cancels the old link (history: Cancelled) and records the new one. |
| `POST ...payments.razorpay.refresh_payment_status` | Unchanged; also records history. |

### For the WordPress plugin (signed, `allow_guest`, POST JSON)

Headers: `X-Agamsale-Timestamp` (unix s), `X-Agamsale-Nonce` (16–64 of `[A-Za-z0-9_-]`, once
only, kept 15 min), `X-Agamsale-Signature` = hex HMAC-SHA256 of
`"{timestamp}\n{nonce}\n{path}\n{raw body}"` with the plugin secret; 5-minute window. Frappe wraps
answers in `{"message": ...}`. Success: `{"ok": true, ...}`. Failure: HTTP 4xx/5xx with
`{"ok": false, "code": "...", "message": "..."}`.

| Endpoint `field_sales_woocommerce.payments.checkout.` | Request | Response |
|---|---|---|
| `ping` | `{}` | `store`, `razorpay: {enabled, mode, keys_set, webhook_secret_set, webhook_url, webhook_events, last_webhook_at}` |
| `test_razorpay` | `{}` | `message` |
| `create_checkout_order` | `{store_order_id}` | `key_id, razorpay_order_id, amount (paise), currency, order_number, prefill{name,email,contact}` |
| `verify_checkout_payment` | `{store_order_id, razorpay_order_id, razorpay_payment_id, razorpay_signature}` | `status: "paid", payment_id, message` |
| `refund_checkout_payment` | `{store_order_id, amount, reason, refund_request_id}` | `refund_id, status (processed/pending), amount` |

Error codes: `UNSIGNED`, `BAD_SIGNATURE`, `EXPIRED`, `REPLAYED` (401); `BAD_REQUEST`,
`ORDER_MISMATCH`, `INVALID_SIGNATURE`, `BAD_AMOUNT` (400); `NOT_PAID` (402); `ORDER_NOT_FOUND`
(404); `NOT_PAYABLE`, `RAZORPAY_DISABLED`, `NOT_WOOCOMMERCE`, `NO_RAZORPAY_PAYMENT`,
`AMOUNT_TOO_HIGH` (409); `RAZORPAY_ERROR`, `STORE_UNAVAILABLE` (502); `NOT_CONNECTED` (503).

### Razorpay webhook

`POST /api/method/field_sales_woocommerce.payments.razorpay.webhook` — HMAC-SHA256 of the raw
body with the webhook secret (`X-Razorpay-Signature`), duplicates skipped by event id and body
hash (`Razorpay Webhook Event`), 500 on unexpected errors so Razorpay retries. Events:
`payment_link.paid`, `payment_link.partially_paid`, `payment_link.expired`,
`payment_link.cancelled`, `payment.captured`, `payment.failed`, `order.paid`,
`refund.processed`, `refund.failed`. Checkout payments are matched by Razorpay order id;
link payments by their link; refunds by refund id (or, for Dashboard refunds, by payment id).

## Behaviour details

- **Duplicate links:** an unexpired Link Sent order returns its current link; *New Payment Link*
  asks for confirmation, cancels the old link on Razorpay and fails safely if it was already paid.
- **Duplicate checkout orders:** the open Razorpay order of a WooCommerce order is reused while
  the total and currency are unchanged (reloads, cancelled popups, failed attempts); a changed
  total gets a new one and the old one is marked Cancelled.
- **Customer cancellation:** closing Razorpay Checkout charges nothing; the pay page offers *Pay
  now* again and *Cancel order*. WooCommerce's hold-stock timeout cancels abandoned orders.
- **Failed payment:** history row Failed with Razorpay's reason; the WooCommerce order becomes
  Failed (still payable) and the customer can retry on the same Razorpay order.
- **Customer never returns:** the `payment.captured` webhook marks the WooCommerce order paid
  through REST (job `payments.checkout.mark_checkout_paid`), skipping orders already paid,
  cancelled, refunded or trashed.
- **Refunds:** idempotent per WooCommerce refund (`wcrefund-<order>-<refund>-<site>`); never more
  than what is left on the payment; Field Order becomes Partially Refunded / Refunded; a
  `refund.failed` webhook restores it. The plugin does not auto-retry refunds (a lost answer
  could otherwise refund twice) and says to check Razorpay first.
- **Temporary failures:** WordPress retries order creation and verification once on network
  errors or 502/503/504. Frappe → WooCommerce marking is a background job; link payments also
  have the hourly `retry_store_payments` and `reconcile_open_links`.
- **Logging:** Sync Log rows for every Razorpay and WooCommerce call (secrets never logged);
  webhook payloads in Razorpay Webhook Event (System Manager only).

## Setup

### Agamsale (Frappe)

1. `bench --site <site> migrate`, then `bench restart` (workers run the new jobs).
2. Agamsale → Settings → **Payments**: Key ID, Key Secret (Test mode keys while testing),
   enable, webhook secret.
3. Settings → **Store**: platform WooCommerce, the store URL and the **WordPress Plugin Secret**
   (from WordPress, below).

### Razorpay Dashboard

1. Accounts & Settings → **Webhooks** → Add: URL from Agamsale's Payments settings (must be public
   HTTPS; use a tunnel such as ngrok for local testing), the same secret, and the nine events above.
2. Test Mode first (`rzp_test_` keys); Live Mode needs Razorpay account activation (KYC) — that
   approval is Razorpay's, not something this app can do.

### WordPress / WooCommerce

1. Upload the plugin zip (folder `wordpress-plugin/` with `field-sales.php`, `includes/`,
   `assets/`) under Plugins → Add New → Upload, and choose *Replace current with uploaded*.
   Requires WooCommerce 8+, PHP 7.4+. Compatible with HPOS and the Cart/Checkout blocks.
2. Settings → **Agamsale**: App URL; copy the secret into Agamsale's Store settings. If this
   server cannot reach the App URL's address (Docker: `localhost` is the container), set
   **Server URL** (e.g. `http://host.docker.internal:8000`) and **Site name**; the Frappe server
   must listen on an address the container can reach.
3. **Test connection** and **Test Razorpay connection**; the page then shows Razorpay mode, keys
   and webhook status.
4. WooCommerce → Settings → Payments → **Razorpay (Agamsale)**: enable, title, description,
   *Open Razorpay automatically*.

No WordPress.org or WooCommerce Marketplace approval is needed to run the plugin on your store.

## Tests

Backend (`bench --site <site> run-tests --app field_sales_woocommerce`): 318 tests, all passing,
including `tests/test_razorpay_checkout.py` (19), `TestPaymentHistory` in
`tests/test_razorpay_links.py`, `TestMarkOrderPaid` in `tests/test_woocommerce_connector.py`.

| Scenario | Covered by |
|---|---|
| Successful payment | `test_verified_payment_marks_the_field_order_paid`, `test_paid_link_records_the_payment_once` |
| Failed payment, retry | `test_failed_then_retried_payment`, `test_webhook_payment_failed_and_unknown_order` |
| Pending / authorized | `test_authorized_payment_is_captured` |
| Expired / cancelled link, replacement | existing `test_razorpay_links` / `test_razorpay_webhook`, `test_new_link_then_replacement_keeps_both_in_history` |
| Duplicate webhook | `test_webhook_records_a_payment_the_customer_never_returned_for`, existing webhook tests |
| Invalid webhook signature | existing `test_razorpay_webhook` |
| Wrong amount / currency | `test_wrong_amount_or_currency_is_not_paid` |
| Bad / replayed / old plugin request | `test_unsigned_wrong_secret_old_or_replayed_requests_are_refused` |
| Network failure (Razorpay), WooCommerce unavailable | `test_razorpay_network_failure_is_a_502_and_records_nothing`, `test_paid_or_missing_order_and_store_down_are_refused` |
| Duplicate order creation | `test_order_amount_comes_from_woocommerce_and_is_reused`, `test_changed_order_total_gets_a_new_razorpay_order` |
| Refunds (partial, full, repeated, too high, failed, Dashboard) | `test_partial_then_full_refund`, `test_repeated_refund_request_refunds_once_and_overrefund_is_refused`, `test_refund_failed_webhook_restores_the_paid_status`, `test_refund_made_in_the_razorpay_dashboard_is_recorded` |
| Closed store order not reopened | `test_cancelled_order_is_refused_untouched` |

Mobile (`flutter test`): `test/payment_history_test.dart` (statuses, history, transaction id).
The pre-existing `staging_scale_benchmark_test.dart` timing test fails with or without these
changes.

Not automated (no PHP test runner here; PHP files pass `php -l`, the request signature was
checked PHP ↔ Python): the gateway against a live WooCommerce, Razorpay Checkout in a browser,
Frappe-down behaviour in WordPress (the API client returns `AGAMSALE_UNREACHABLE`; the pay page
shows "Payments are temporarily unavailable" with *Try again*). Run these in Razorpay Test Mode
with test cards/UPI before going live.

## Release

1. Merge the backend branch; `bench --site <site> migrate`; `bench build --app field_sales_woocommerce`
   (or `yarn build` in `frontend/`); `bench restart`.
2. Upload plugin 1.3.0 to WordPress.
3. Add the new webhook events in the Razorpay Dashboard.
4. Release the mobile app build from `feat/payment-history` (older app versions keep working:
   the new fields are additions).
