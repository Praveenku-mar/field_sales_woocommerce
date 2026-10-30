app_name = "field_sales_woocommerce"
app_title = "Field Sales Woocommerce"
app_publisher = "Agamnxt Technologies"
app_description = "Field Sales app dedicated to WooCommerce stores"
app_email = "praveenkumar@agamnxt.com"
app_license = "mit"

use_json_request_body = True

add_to_apps_screen = [
	{
		"name": "field_sales_woocommerce",
		"logo": "/assets/field_sales_woocommerce/images/logo.svg",
		"title": "Field Sales",
		"route": "/fieldsales",
		"has_permission": "field_sales_woocommerce.www.fieldsales.has_app_access",
	}
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

fixtures = [
	{"dt": "Print Format", "filters": [["name", "in", ["Field Sales Order", "Field Sales Invoice"]]]},
]
