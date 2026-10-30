// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.query_reports["Rep Performance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "compare_with",
			label: __("Compare With"),
			fieldtype: "Select",
			options: ["None", "Previous Period", "Same Period Last Year"],
			default: "Previous Period",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Sales Rep", "Territory", "City", "Retailer Type", "Day", "Week", "Month"],
			default: "Sales Rep",
		},
		{
			fieldname: "sales_rep",
			label: __("Sales Rep"),
			fieldtype: "MultiSelectList",
			get_data: (txt) => frappe.db.get_link_options("Sales Rep", txt),
		},
		{
			fieldname: "territory",
			label: __("Territory"),
			fieldtype: "Autocomplete",
			// Territory is free text on Sales Rep: offer the values in use.
			get_query: () => ({ query: "field_sales_woocommerce.api.get_territories" }),
		},
		{
			fieldname: "city",
			label: __("City"),
			fieldtype: "Autocomplete",
			get_query: () => ({ query: "field_sales_woocommerce.api.get_retailer_cities" }),
		},
		{
			fieldname: "retailer_type",
			label: __("Retailer Type"),
			fieldtype: "Select",
			options: ["", "Kirana", "Supermarket", "Wholesaler", "Medical", "Other"],
		},
		{
			fieldname: "show_only_below_target",
			label: __("Only Below Target"),
			fieldtype: "Check",
		},
	],

	// Tree view (managers over their reps) when grouped by Sales Rep; other
	// groupings return rows without "indent" and render as a flat table.
	tree: true,
	name_field: "group_key",
	parent_field: "parent_key",
	initial_depth: 1,

	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;

		const field = column.fieldname;

		if (field === "group" && data.is_team) {
			return `<b>${formatted}</b>`;
		}

		if (field === "achievement_pct" && data.achievement_pct != null) {
			const pct = data.achievement_pct;
			const color = pct < 70 ? "red" : pct < 100 ? "orange" : "green";
			return colored(formatted, color, true);
		}

		if (field === "completion_pct" && data.completion_pct != null && data.completion_pct < 80) {
			return colored(formatted, "red", true);
		}

		if (field === "value_delta_pct" && data.value_delta_pct != null) {
			const delta = data.value_delta_pct;
			const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "";
			return colored(`${arrow} ${formatted}`, delta > 0 ? "green" : delta < 0 ? "red" : "gray");
		}

		if (field === "failed_syncs" && data.failed_syncs > 0) {
			return colored(formatted, "red", true);
		}

		// Drill-down: the visits / orders behind the number, as a filtered list.
		const drill_target = { planned_visits: "visits", completed_visits: "visits", orders: "orders" }[field];
		if (drill_target && value && data.drill && data.drill[drill_target]) {
			const filters = { ...data.drill[drill_target] };
			if (field === "completed_visits") filters.status = "Completed";
			const doctype = drill_target === "orders" ? "Field Order" : "Visit";
			return `<a href="#" class="fs-drill" data-doctype="${doctype}"
				data-filters="${frappe.utils.escape_html(JSON.stringify(filters))}">${formatted}</a>`;
		}

		return formatted;
	},

	onload(report) {
		// One delegated handler: cells are re-rendered on every refresh / scroll.
		report.page.wrapper.on("click", "a.fs-drill", function (e) {
			e.preventDefault();
			const $link = $(this);
			frappe.route_options = JSON.parse($link.attr("data-filters"));
			frappe.set_route("List", $link.attr("data-doctype"));
		});
	},
};

function colored(html, color, bold = false) {
	return `<span style="color: var(--${color}-600)${bold ? "; font-weight: 600" : ""}">${html}</span>`;
}
