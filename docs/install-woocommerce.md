# Installing Field Sales for a WooCommerce store

Two halves: the **app** (a Frappe site) and the **store** (WooCommerce). They talk
over the WooCommerce REST API; nothing is installed inside WordPress.

## 1. Set up the app

```bash
# in your bench (Frappe v17)
bench get-app https://github.com/<your-org>/field_sales   # or a local path
bench new-site sales.example.com
bench --site sales.example.com install-app field_sales
bench --site sales.example.com migrate          # doctypes + fixtures (print format)
bench --site sales.example.com enable-scheduler # product sync runs on a schedule
```

The app's frontend is prebuilt and ships with the repo — no `npm build` needed to
install. Open the app at `https://sales.example.com/fieldsales` and sign in.

> Everything below was verified against WooCommerce 11 with the connector's own
> test suite and a live store.

## 2. Prepare the WooCommerce store

1. **Permalinks**: WordPress → Settings → Permalinks → anything but "Plain"
   (the REST API lives at `/wp-json/`).
2. **API key**: WooCommerce → Settings → Advanced → REST API → *Add key*,
   permissions **Read/Write**. Copy the **Consumer Key** and **Consumer Secret**
   (the secret is shown once). The connector signs requests with OAuth1, so this
   works on HTTPS and plain HTTP alike — but use HTTPS in production.
3. **Timezone** (recommended): Settings → General → Timezone → your real zone.
   The app follows the store's currency and timezone, and WordPress defaults to UTC.

## 3. Connect

As a System Manager, open **Store Connection** (sidebar, bottom):

1. **Platform**: WooCommerce — the form switches to Consumer Key / Secret / API Version.
2. **Store URL**: `https://your-store.example.com` (scheme optional).
3. Paste the **Consumer Key** and **Consumer Secret**, then **Save Changes**.
4. **Test Connection** — reports the store, its currency and timezone.
5. **Fetch Locations** → pick **WooCommerce Stock** → **Set Default**. WooCommerce
   has a single stock pool; the app shows it as one location.
6. First product sync: open **Products** → **Sync Products** (afterwards the
   scheduler re-syncs per *Product Sync Frequency*, default every 30 minutes).

Currency and timezone: saving the connection records the store's values, and with
**Match Store Currency and Timezone** on (default) the site adopts them — the
store is the source of truth for money, so the site must use its currency.

## 4. People and roles

| Role | What they can do |
|---|---|
| Field Sales Rep | visits, orders, read-only master data |
| Sales Manager | everything a rep does + edit customers/visits, reports |
| System Manager | settings, store connection, sync logs |

Each user needs one of these roles plus a **Sales Rep** record (Field Sales →
Sales Reps → New). Reps see data through their own hierarchy.

## 5. Optional: webhooks for live stock updates

Stock is refreshed on every product sync and checked live at order submit, so
webhooks are optional. For push updates, create them in WooCommerce → Settings →
Advanced → **Webhooks** pointing at:

```
https://sales.example.com/api/method/field_sales.api.webhooks.receive
```

| Woo topic | Effect |
|---|---|
| `product.updated` | stock and price refresh |
| `product.deleted` | product archived |
| `order.updated` | payment / fulfilment state |

WooCommerce signs each delivery; the app verifies signatures with the Consumer
Secret already configured — no extra setup.

## 6. Daily flow

1. Rep opens the app (phone or desktop), plans visits.
2. At the shop: check in (geofenced), take the order (product search with live
   stock), submit — the order lands on the store (Draft mode → `pending`,
   Confirmed → `processing`), the customer is created/updated there, and the
   confirmation email goes out with a Razorpay payment link when the customer
   has an email.
3. Managers watch the dashboard, sync failures and reports.

## 7. Optional: the app inside wp-admin

`wordpress-plugin/field-sales.php` is a small WordPress plugin that adds a
**Field Sales** item to wp-admin embedding the app:

1. Copy `wordpress-plugin/` to the store's `wp-content/plugins/` (or zip it and
   upload under Plugins → Add New).
2. Activate **Field Sales**, then set your app URL on Settings → Field Sales.
3. The app page allows framing from the connected store's origin automatically.

If the browser asks you to sign in repeatedly inside the frame, use the page's
*Open in a new tab* button — browsers can restrict sign-in inside cross-site
frames. Over HTTPS with third-party cookies allowed the frame keeps you signed in.
