// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.ui.form.on("Platform Product", {
	refresh: function (frm) {
		_show_stock_by_location(frm);
	},
});

// Read-only table of the product's stock at each store location (Product Stock rows).
function _show_stock_by_location(frm) {
	if (frm.is_new() || !frm.doc.track_inventory) return;

	frappe.db
		.get_list("Product Stock", {
			filters: { product: frm.doc.name },
			fields: ["location_name", "location_id", "available_qty", "stock_updated_at"],
			order_by: "location_name asc",
			limit: 100,
		})
		.then(function (rows) {
			if (!rows.length) return;
			var body = rows
				.map(function (row) {
					return (
						"<tr><td>" +
						frappe.utils.escape_html(row.location_name || row.location_id) +
						"</td><td class='text-right'>" +
						format_number(row.available_qty, null, 0) +
						"</td><td>" +
						(row.stock_updated_at ? frappe.datetime.str_to_user(row.stock_updated_at) : "") +
						"</td></tr>"
					);
				})
				.join("");
			frm.dashboard.add_section(
				"<table class='table table-bordered table-sm'><thead><tr><th>" +
					__("Location") +
					"</th><th class='text-right'>" +
					__("Available") +
					"</th><th>" +
					__("Updated") +
					"</th></tr></thead><tbody>" +
					body +
					"</tbody></table>",
				__("Stock by Location")
			);
		});
}
