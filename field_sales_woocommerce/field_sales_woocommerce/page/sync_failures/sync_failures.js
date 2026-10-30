// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

frappe.pages["sync-failures"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Sync Failures"),
		single_column: true,
	});
	wrapper.sync_failures = new SyncFailures(page);
};

frappe.pages["sync-failures"].on_page_show = function (wrapper) {
	wrapper.sync_failures && wrapper.sync_failures.refresh();
};

const TABS = {
	orders: {
		label: __("Field Orders"),
		doctype: "Field Order",
		party: (row) => row.retailer_name || row.retailer,
		retry: (name) =>
			frappe.call({ method: "field_sales_woocommerce.api.retry_sync", args: { field_order: name } }),
	},
	retailers: {
		label: __("Retailers"),
		doctype: "Retailer",
		party: (row) => row.retailer_name,
		retry: (name) =>
			frappe.call({ method: "field_sales_woocommerce.api.sync_retailers", args: { names: [name] } }),
	},
};

class SyncFailures {
	constructor(page) {
		this.page = page;
		this.active = "orders";
		this.data = { orders: [], retailers: [] };

		this.page.set_primary_action(__("Retry All"), () => this.retry_all(), "refresh");
		this.page.set_secondary_action(__("Refresh"), () => this.refresh());

		this.$body = $(`<div class="sync-failures">
			<ul class="nav nav-tabs mb-3" role="tablist"></ul>
			<div class="sync-failures-table"></div>
		</div>`).appendTo(this.page.main);
		this.$tabs = this.$body.find(".nav-tabs");
		this.$table = this.$body.find(".sync-failures-table");

		this.$body.on("click", "[data-tab]", (e) => {
			e.preventDefault();
			this.active = $(e.currentTarget).attr("data-tab");
			this.render();
		});
		this.$body.on("click", "[data-action=retry]", (e) => this.retry_row($(e.currentTarget)));

		// A push finished somewhere: refresh (debounced, as Retry All finishes many at once).
		const on_sync = frappe.utils.debounce(() => this.is_visible() && this.refresh(), 1000);
		frappe.realtime.on("field_order_synced", on_sync);
		frappe.realtime.on("retailer_synced", on_sync);
	}

	is_visible() {
		return frappe.get_route_str() === "sync-failures";
	}

	refresh() {
		return frappe.xcall("field_sales_woocommerce.api.get_sync_failures").then((data) => {
			this.data = data;
			this.render();
		});
	}

	render() {
		this.$tabs.html(
			Object.entries(TABS)
				.map(([key, tab]) => {
					const count = this.data[key].length;
					return `<li class="nav-item">
						<a class="nav-link ${key === this.active ? "active" : ""}" href="#" data-tab="${key}">
							${tab.label}
							<span class="badge ${count ? "badge-danger" : "badge-secondary"} ml-1">${count}</span>
						</a>
					</li>`;
				})
				.join("")
		);
		this.$table.html(this.table_html(this.active));
	}

	table_html(key) {
		const tab = TABS[key];
		const rows = this.data[key];
		if (!rows.length) {
			return `<div class="text-muted text-center p-5">${__("No failed {0}", [tab.label.toLowerCase()])}</div>`;
		}

		const esc = frappe.utils.escape_html;
		const body = rows
			.map((row) => {
				const link = `/app/${frappe.router.slug(tab.doctype)}/${encodeURIComponent(row.name)}`;
				return `<tr data-name="${esc(row.name)}">
					<td><a href="${link}">${esc(row.name)}</a></td>
					<td>
						<div>${esc(tab.party(row) || "")}</div>
						<div class="text-muted small">${esc(row.sales_rep_name || row.sales_rep || "")}</div>
					</td>
					<td class="text-nowrap" title="${esc(row.last_sync_attempt || "")}">
						${row.last_sync_attempt ? frappe.datetime.comment_when(row.last_sync_attempt) : "—"}
					</td>
					<td class="text-right">${cint(row.sync_attempts)}</td>
					<td class="text-danger" style="max-width: 420px; white-space: normal">${esc(row.sync_error || "")}</td>
					<td class="text-nowrap text-right">
						<button class="btn btn-xs btn-default" data-action="retry">${__("Retry")}</button>
						<a class="btn btn-xs btn-default" href="${link}">${__("Open")}</a>
					</td>
				</tr>`;
			})
			.join("");

		return `<div class="table-responsive"><table class="table table-bordered table-hover">
			<thead><tr>
				<th>${__("Name")}</th>
				<th>${__("Retailer / Rep")}</th>
				<th>${__("Last Attempt")}</th>
				<th class="text-right">${__("Attempts")}</th>
				<th>${__("Error")}</th>
				<th></th>
			</tr></thead>
			<tbody>${body}</tbody>
		</table></div>`;
	}

	retry_row($btn) {
		const name = $btn.closest("tr").attr("data-name");
		$btn.prop("disabled", true).text(__("Queued"));
		TABS[this.active]
			.retry(name)
			.then(() => frappe.show_alert({ message: __("Retry queued for {0}", [name]), indicator: "blue" }))
			.catch(() => $btn.prop("disabled", false).text(__("Retry")));
	}

	retry_all() {
		frappe.confirm(__("Retry every failed order and retailer?"), () => {
			frappe
				.call({ method: "field_sales_woocommerce.api.retry_all_sync_failures", freeze: true })
				.then(({ message: r }) => {
					frappe.show_alert({ message: r.message, indicator: "blue" });
					this.$table.find("[data-action=retry]").prop("disabled", true).text(__("Queued"));
				});
		});
	}
}
