# Installing Field Sales for a WooCommerce store

Two halves: the **app** (a Frappe site) and the **store** (WooCommerce). They talk
over the WooCommerce REST API; nothing is installed inside WordPress.

## 1. Set up the app

```bash
# in your bench (Frappe v17)
bench get-app https://github.com/<your-org>/field_sales_woocommerce   # or a local path
bench new-site sales.example.com
bench --site sales.example.com install-app field_sales_woocommerce
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

## 5. Webhooks for live updates (automatic)

Stock is refreshed on every product sync and checked live at order submit, so
webhooks only make updates arrive sooner. **Test Connection sets them up**: it
registers one WooCommerce webhook per topic below, delivering to

```
https://sales.example.com/api/method/field_sales_woocommerce.api.webhooks.receive
```

and the result shows in its banner (and in Sync Log, operation *Register
Webhooks*).

| Woo topic | Effect |
|---|---|
| `product.updated`, `product.restored` | stock and price refresh |
| `product.deleted` | product archived |
| `order.updated`, `order.restored` | payment / fulfilment state |
| `order.deleted` | treated as cancelled |

Each webhook is signed with the Consumer Secret, which the app verifies. Running
Test Connection again reuses the same webhooks: it turns them back on (WooCommerce
disables a webhook after repeated failed deliveries) and resets their secret, so
it also repairs webhooks added by hand earlier. Webhooks pointing elsewhere are
left alone.

The address must be reachable from the WooCommerce server: on a local setup where
it is not, deliveries fail and WooCommerce disables the webhooks after a few
tries; nothing else is affected.

## 6. Daily flow

1. Rep opens the app (phone or desktop), plans visits.
2. At the shop: check in (geofenced), take the order (product search with live
   stock), submit — the order lands on the store (Draft mode → `pending`,
   Confirmed → `processing`), the customer is created/updated there, and the
   confirmation email goes out with a Razorpay payment link when the customer
   has an email.
3. Managers watch the dashboard, sync failures and reports.

## 7. Optional: the app inside wp-admin

`wordpress-plugin/field-sales.php` is a small WordPress plugin that adds an
**Agamsale** item to wp-admin embedding the app:

1. Copy `wordpress-plugin/` to the store's `wp-content/plugins/` (or zip it and
   upload under Plugins → Add New).
2. Activate **Agamsale**, then set your app URL on Settings → Agamsale.
3. Copy the **secret** shown on Settings → Agamsale and paste it in the app under
   Settings → Store Connection → **WordPress Plugin Secret**, then Save.
4. The app page allows framing from the connected store's origin automatically
   (Store Connection → Store URL must be this WordPress site).

**Sign-in.** Inside wp-admin the app signs you in as the Agamsale user with the
**same email** as your WordPress account; that user needs a Field Sales role
(Sales Rep, Sales Manager or System Manager). The plugin hands the app a 5-minute
token signed with the shared secret, so no cookies are needed and it works in
every browser, including Safari and with third-party cookies blocked. A WordPress
user with no matching Agamsale user sees "no Agamsale access". *Regenerate secret*
on the plugin page invalidates the old one: paste the new one in the app.

**Same site alternative.** Serving the app on a subdomain of the store (e.g.
`sales.shop.com` for `shop.com`) makes the app's own sign-in cookie work in the
frame too; the settings page says which case you are in. If you are already
signed in to the app in that browser, that sign-in is used instead of the token.

**Location, camera, clipboard.** The plugin's frame allows them, so check-in
location and the barcode scanner work inside wp-admin (the browser still asks
for permission). Both sites must be on HTTPS (plain `http://localhost` works for
local testing).

Not in the frame: live updates (pages poll every 30 s instead) and installing
the app — use *Open in a new tab* for those.

## 8. Background jobs

With the scheduler enabled (section 1), the app runs:

| When | Job |
|---|---|
| every 30 min | Product sync, at Store Connection's *Product Sync Frequency* |
| every 15 min | Re-push orders that failed (up to 5 attempts) or got stuck in Pending |
| hourly | Re-check Razorpay links whose webhook never arrived; retry marking paid orders paid on the store |
| daily | Purge old Sync Logs; re-match the store's currency and time zone; anonymise inactive retailers (off unless *Anonymise Inactive Retailers After* is set) |
| 20:00 | Daily team summary email to each manager (needs an outgoing Email Account) |
| 1st of the month, 02:00 | Draft commission statements for last month |

**Upgrading a site from before these jobs existed** (earlier releases did not schedule
them): their first run works through everything that piled up. Before `bench migrate`, see
what that will be:

```bash
bench --site sales.example.com execute field_sales_woocommerce.utils.scheduler_check.report
```

It lists the orders that will be re-pushed to the store, the Sync Logs that will be
deleted, the retailers that will be anonymised and whether the summary email can be sent.
Cancel any order that should not reach the store (or set its *Sync Attempts* to 5), then
`bench migrate`. Run it again afterwards: *not registered* should say *none*.
