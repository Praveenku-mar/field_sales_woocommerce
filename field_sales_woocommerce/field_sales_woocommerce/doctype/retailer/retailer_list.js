// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Retailer"] = {
	add_fields: ["sync_status", "status", "last_visit"],

	onload(listview) {
		listview.page.add_actions_menu_item(__("Sync to Store"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;

			frappe
				.call({ method: "field_sales_woocommerce.api.sync_retailers", args: { names }, freeze: true })
				.then(({ message: r }) => {
					frappe.show_alert({ message: r.message, indicator: "blue" });
					listview.refresh();
				});
		});
	},

	get_indicator(doc) {
		if (doc.status === "Inactive") {
			return [__("Inactive"), "gray", "status,=,Inactive"];
		}
		const indicators = {
			Synced: [__("Synced"), "green", "sync_status,=,Synced"],
			Pending: [__("Sync Pending"), "orange", "sync_status,=,Pending"],
			Failed: [__("Sync Failed"), "red", "sync_status,=,Failed"],
			Draft: [__("Not Synced"), "gray", "sync_status,=,Draft"],
		};
		return indicators[doc.sync_status] || indicators.Draft;
	},

	formatters: {
		// "3 days ago", red once a retailer has gone 30 days without a visit.
		last_visit(value) {
			if (!value) return `<span class="text-muted">${__("Never")}</span>`;
			const days = frappe.datetime.get_day_diff(frappe.datetime.get_today(), value);
			const text = frappe.datetime.prettyDate(value);
			return days > 30 ? `<span style="color: var(--red-600)">${text}</span>` : text;
		},
	},

	// Per-row quick action.
	button: {
		show: (doc) => doc.status !== "Inactive",
		get_label: () => __("Plan Visit"),
		get_description: (doc) => __("Plan a visit to {0}", [doc.retailer_name || doc.name]),
		action: (doc) => frappe.new_doc("Visit", { retailer: doc.name }),
	},
};
