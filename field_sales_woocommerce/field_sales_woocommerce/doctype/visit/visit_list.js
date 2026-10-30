// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Visit"] = {
	add_fields: ["status", "within_geofence", "check_in_time", "distance_from_retailer_m"],

	onload(listview) {
		listview.page.add_inner_button(__("Today"), () => {
			listview.filter_area.clear(false).then(() =>
				listview.filter_area.add([["Visit", "visit_date", "=", frappe.datetime.get_today()]])
			);
		});
	},

	get_indicator(doc) {
		const indicators = {
			Planned: [__("Planned"), "blue", "status,=,Planned"],
			"In Progress": [__("In Progress"), "orange", "status,=,In Progress"],
			Completed: [__("Completed"), "green", "status,=,Completed"],
			Cancelled: [__("Cancelled"), "red", "status,=,Cancelled"],
		};
		return indicators[doc.status] || indicators.Planned;
	},

	formatters: {
		time_on_site_min(value, df, doc) {
			return doc.status === "Completed" ? __("{0} min", [value || 0]) : "";
		},
		// Only meaningful once checked in; shows the distance on hover.
		within_geofence(value, df, doc) {
			if (!doc.check_in_time) return "";
			const distance = __("{0} m from the retailer", [Math.round(doc.distance_from_retailer_m || 0)]);
			return value
				? `<span style="color: var(--green-600)" title="${distance}">${__("Inside")}</span>`
				: `<span style="color: var(--orange-600)" title="${distance}">${__("Outside")}</span>`;
		},
	},
};
