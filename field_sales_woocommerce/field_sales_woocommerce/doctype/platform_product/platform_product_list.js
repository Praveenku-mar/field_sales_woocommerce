// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

// Stock at or below this shows as "Low Stock".
const LOW_STOCK = 5;

frappe.listview_settings["Platform Product"] = {
	add_fields: ["available_qty", "track_inventory", "status"],

	get_indicator(doc) {
		if (doc.status !== "Active") return [__(doc.status), "gray", `status,=,${doc.status}`];
		if (!doc.track_inventory) return [__("Not Tracked"), "blue", "track_inventory,=,0"];
		if (doc.available_qty <= 0) return [__("Out of Stock"), "red", "available_qty,<=,0"];
		if (doc.available_qty <= LOW_STOCK) return [__("Low Stock"), "orange", `available_qty,<=,${LOW_STOCK}`];
		return [__("In Stock"), "green", `available_qty,>,${LOW_STOCK}`];
	},

	formatters: {
		available_qty(value, df, doc) {
			if (!doc.track_inventory) return `<span class="text-muted">—</span>`;
			const color = value <= 0 ? "red" : value <= LOW_STOCK ? "orange" : "green";
			return `<span style="color: var(--${color}-600)">${format_number(value, null, 0)}</span>`;
		},
	},

	onload(listview) {
		if (!frappe.user.has_role(["System Manager", "Sales Manager"])) return;

		// Not page.set_primary_action: the list view resets its primary button
		// ("Add Platform Product") after onload and whenever selection clears.
		listview.page.add_inner_button(
			__("Sync Products"),
			() => {
				frappe
					.call({ method: "field_sales_woocommerce.api.enqueue_product_sync", freeze: true })
					.then(({ message: r }) => {
						frappe.show_alert({ message: r.message, indicator: r.queued ? "blue" : "orange" });
					});
			},
			null,
			"primary"
		);

		// Re-bind on every load so only the current list view reacts.
		frappe.realtime.off("product_sync_done");
		frappe.realtime.on("product_sync_done", (data) => {
			if (data.ok) {
				frappe.show_alert(
					{
						message: __("Product sync done: {0} created, {1} updated, {2} unchanged, {3} archived", [
							data.created,
							data.updated,
							data.unchanged,
							data.archived,
						]),
						indicator: "green",
					},
					10
				);
			} else {
				frappe.show_alert(
					{
						message: __("Product sync failed: {0}", [frappe.utils.escape_html(data.message || "")]),
						indicator: "red",
					},
					10
				);
			}
			listview.refresh();
		});
	},

	get_indicator: function (doc) {
		if (doc.status === "Archived") {
			return [__("Archived"), "gray", "status,=,Archived"];
		}
		if (!doc.track_inventory) {
			return [__("Not Tracked"), "blue", "track_inventory,=,0"];
		}

		const qty = flt(doc.available_qty);

		if (qty <= 0) {
			return [__("Out of Stock"), "red", "available_qty,<=,0"];
		} else if (qty < 20) {
			return [__("Low Stock"), "orange", "available_qty,<,20"];
		} else {
			return [__("In Stock"), "green", "available_qty,>=,20"];
		}
	},
};
