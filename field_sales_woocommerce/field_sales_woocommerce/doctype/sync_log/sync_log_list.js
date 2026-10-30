// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.listview_settings["Sync Log"] = {
	// Guarantee status is always present in the list payload.
	add_fields: ["status"],

	get_indicator: function (doc) {
		const indicators = {
			Success:  [__("Success"),  "green",  "status,=,Success"],
			Retrying: [__("Retrying"), "orange", "status,=,Retrying"],
			Failed:   [__("Failed"),   "red",    "status,=,Failed"],
		};

		return indicators[doc.status] || [doc.status, "gray", `status,=,${doc.status}`];
	},
};
