app_name = "field_sales_woocommerce"
app_title = "Agamsale"
app_publisher = "Agamnxt Technologies"
app_description = "Agamsale app dedicated to WooCommerce stores"
app_email = "praveenkumar@agamnxt.com"
app_license = "mit"

use_json_request_body = True

add_to_apps_screen = [
	{
		"name": "field_sales_woocommerce",
		"logo": "/assets/field_sales_woocommerce/images/agamsale-logo.png",
		"title": "Agamsale",
		"route": "/fieldsales",
		"has_permission": "field_sales_woocommerce.www.fieldsales.has_app_access",
	}
]

# Serve the Vue app shell for frontend routes and its short alias.
website_route_rules = [
	{"from_route": "/fieldsales/<path:app_path>", "to_route": "fieldsales"},
	{"from_route": "/fs", "to_route": "fieldsales"},
	{"from_route": "/fs/<path:app_path>", "to_route": "fieldsales"},
]

permission_query_conditions = {
	"Visit": "field_sales_woocommerce.permissions.visit_query",
	"Sales Rep": "field_sales_woocommerce.permissions.sales_rep_query",
	"Field Order": "field_sales_woocommerce.permissions.field_order_query",
	"Retailer": "field_sales_woocommerce.permissions.retailer_query",
	"Commission Entry": "field_sales_woocommerce.permissions.commission_entry_query",
	"Commission Statement": "field_sales_woocommerce.permissions.commission_statement_query",
}

has_permission = {
	"Visit": "field_sales_woocommerce.permissions.visit_has_permission",
	"Sales Rep": "field_sales_woocommerce.permissions.sales_rep_has_permission",
	"Field Order": "field_sales_woocommerce.permissions.field_order_has_permission",
	"Retailer": "field_sales_woocommerce.permissions.retailer_has_permission",
	"Commission Entry": "field_sales_woocommerce.permissions.commission_entry_has_permission",
	"Commission Statement": "field_sales_woocommerce.permissions.commission_statement_has_permission",
}
after_request = ["field_sales_woocommerce.utils.guards.after_request"]

# The app inside wp-admin (Agamsale WordPress plugin): the page boots without a login
# redirect and API calls sign in with the plugin's token (wordpress_embed.py).
field_sales_embedded_page = ["field_sales_woocommerce.wordpress_embed.page_context"]
auth_hooks = ["field_sales_woocommerce.wordpress_embed.authenticate"]

fixtures = [
	{"dt": "Print Format", "filters": [["name", "in", ["Field Sales Order", "Field Sales Invoice"]]]},
]

doc_events = {
	# Push bell notifications made by push.notify.notify_users to the mobile app.
	"Notification Log": {"after_insert": "field_sales_woocommerce.push.notify.on_notification_log_insert"},
}

# Background jobs. Each job checks its own switch (Store Connection enabled, sync frequency,
# Razorpay / commission on, anonymise days > 0), so a site that has not set one up does nothing.
scheduler_events = {
	# Purge Success Sync Logs older than 30 days, Failed ones after failed_log_retention_days.
	"daily": [
		"field_sales_woocommerce.field_sales_woocommerce.doctype.sync_log.sync_log.clear_old_logs",
		# Keep the site's currency and time zone the same as the store's.
		"field_sales_woocommerce.sync.shop_settings.scheduled_sync",
		# Store Connection retailer_inactive_anonymise_days (0 = off).
		"field_sales_woocommerce.utils.anonymise.anonymise_inactive_retailers",
	],
	# Razorpay payment links whose webhook never arrived (Link Sent, last 7 days, 50 a run).
	"hourly": [
		"field_sales_woocommerce.payments.razorpay.reconcile_open_links",
		# Confirmed store orders paid on Razorpay that the store does not show as paid yet.
		"field_sales_woocommerce.sync.orders.retry_store_payments",
	],
	# Ticks every 30 minutes; scheduled_sync itself honours Store Connection's sync frequency.
	# No link_completed_drafts: WooCommerce has no draft orders (Draft mode makes a `pending` order).
	"cron": {
		"*/30 * * * *": [
			"field_sales_woocommerce.sync.products.scheduled_sync",
		],
		# Re-push Field Orders that failed (up to 5 attempts) or lost their job.
		"*/15 * * * *": [
			"field_sales_woocommerce.sync.orders.retry_failed_orders",
		],
		# Daily team summary to each manager, in the system timezone.
		"0 20 * * *": [
			"field_sales_woocommerce.daily_summary.send_daily_summary",
		],
		# Draft Commission Statements for last month, on the 1st.
		"0 2 1 * *": [
			"field_sales_woocommerce.commission.statements.generate_monthly_statements",
		],
	},
}
