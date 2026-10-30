// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

// ---------------------------------------------------------------------------
// Parent form handlers
// ---------------------------------------------------------------------------

frappe.ui.form.on("Field Order", {
	setup: function (frm) {
		// Filter Visit dropdown to the currently selected Retailer.
		// set_query uses a closure so it always reads the live frm.doc value.
		frm.set_query("visit", function () {
			return {
				filters: { retailer: frm.doc.retailer || "__none__" },
			};
		});
	},

	onload: function (frm) {
		_listen_for_sync(frm);
	},

	refresh: function (frm) {
		_set_location_options(frm);
		_highlight_stock_warnings(frm);
		_show_order_summary(frm);
		_add_stock_button(frm);
		_show_sync_status(frm);
		_add_payment_buttons(frm);
		_show_discount_limit(frm);
		_lock_rate(frm);
	},

	// Live stock check right before submit; returning the promise makes the
	// form wait for it, and frappe.validated = false cancels the submit.
	before_submit: function (frm) {
		return _refresh_stock(frm)
			.then(function (rows) {
				var short = rows.filter(function (row) {
					return row.stock_warning;
				});
				if (short.length) {
					frappe.validated = false;
					frappe.msgprint({
						title: __("Not Enough Stock"),
						indicator: "red",
						message: _stock_shortage_html(short),
					});
				}
			})
			.catch(function () {
				// The server error (e.g. store unreachable) is already shown.
				frappe.validated = false;
			});
	},

	retailer: function (frm) {
		if (!frm.doc.retailer) {
			frm.set_value("visit", "");
			frm.set_value("sales_rep", "");
			return;
		}
		if (!frm.doc.visit) {
			_set_rep_from_retailer(frm);
			return;
		}
		// Keep the visit (and its rep) when it is to this retailer, e.g. an
		// order opened from the visit; otherwise it belongs to the old retailer.
		frappe.db.get_value("Visit", frm.doc.visit, ["retailer", "sales_rep"]).then(function (r) {
			var visit = r.message || {};
			if (visit.retailer === frm.doc.retailer) {
				if (visit.sales_rep) frm.set_value("sales_rep", visit.sales_rep);
			} else {
				frm.set_value("visit", "");
				_set_rep_from_retailer(frm);
			}
		});
	},

	sales_rep: function (frm) {
		_show_discount_limit(frm);
	},

	visit: function (frm) {
		if (!frm.doc.visit) return;
		// Picking a visit fills its retailer and rep.
		frappe.db.get_value("Visit", frm.doc.visit, ["retailer", "sales_rep"]).then(function (r) {
			var visit = r.message || {};
			if (visit.sales_rep) frm.set_value("sales_rep", visit.sales_rep);
			if (visit.retailer && visit.retailer !== frm.doc.retailer) frm.set_value("retailer", visit.retailer);
		});
	},

	// A new location means different stock: save and re-check it live on a draft.
	location_id: function (frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.doc.location_id) return;
		frm.save().then(function () {
			return _refresh_stock(frm);
		});
	},

	tax_percent: function (frm) {
		_recalc_totals(frm);
	},

	discount_percent: function (frm) {
		_recalc_totals(frm);
	},
});

// ---------------------------------------------------------------------------
// Child table handlers
// ---------------------------------------------------------------------------

frappe.ui.form.on("Field Order Item", {
	// Stock at the order's location (the row field is not fetched from the product, which
	// only holds the default location's stock). The server sets it again on save.
	product: function (frm, cdt, cdn) {
		var row = locals[cdt][cdn];
		if (!row.product) return;
		frappe
			.call({
				method: "field_sales_woocommerce.api.get_live_stock",
				args: { products: [row.product], location_id: frm.doc.location_id || null },
			})
			.then(function (r) {
				var live = r.message || {};
				if (row.product in live) {
					frappe.model.set_value(cdt, cdn, "available_qty", live[row.product]);
					_highlight_stock_warnings(frm);
				}
			});
	},

	qty: function (frm, cdt, cdn) {
		_recalc_row(frm, cdt, cdn);
	},

	rate: function (frm, cdt, cdn) {
		_recalc_row(frm, cdt, cdn);
	},

	items_remove: function (frm) {
		_recalc_totals(frm);
		_highlight_stock_warnings(frm);
	},
});

// ---------------------------------------------------------------------------
// Store sync: stock check, sync status, retry
// ---------------------------------------------------------------------------

function _add_stock_button(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 0) return;

	frm.add_custom_button(__("Refresh Stock"), function () {
		var save = frm.is_dirty() ? frm.save() : Promise.resolve();
		save.then(function () {
			return _refresh_stock(frm);
		}).then(function (rows) {
			var short = rows.filter(function (row) {
				return row.stock_warning;
			});
			frappe.show_alert({
				message: short.length
					? __("{0} item(s) exceed available stock", [short.length])
					: __("Stock is available for all items"),
				indicator: short.length ? "red" : "green",
			});
		});
	});
}

/**
 * Fetch live stock on the server (which saves it on the order), then reload
 * the form so it holds the saved version. Resolves with the item rows.
 */
function _refresh_stock(frm) {
	return frappe
		.call({
			method: "field_sales_woocommerce.api.refresh_stock",
			args: { field_order: frm.doc.name },
			freeze: true,
			freeze_message: __("Checking live stock…"),
		})
		.then(function (r) {
			return frm.reload_doc().then(function () {
				return r.message || [];
			});
		});
}

function _stock_shortage_html(rows) {
	var lines = rows.map(function (row) {
		return __("Row {0}: {1} — ordered {2}, available {3}", [
			row.idx,
			frappe.utils.escape_html(row.product_title || row.product),
			row.qty,
			row.available_qty,
		]);
	});
	return lines.join("<br>");
}

// Razorpay "Payment" button group on submitted orders. get_payment_config is called once
// per form; the buttons are rebuilt from it on every refresh.
function _add_payment_buttons(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (!frm._payment_config) {
		frm._payment_config = frappe
			.call({ method: "field_sales_woocommerce.payments.razorpay.get_payment_config", type: "GET" })
			.then(function (r) {
				return r.message || {};
			});
	}
	var name = frm.doc.name;
	frm._payment_config.then(function (config) {
		// The form may have moved on (another order, or cancelled) while this was loading.
		if (!config.enabled || frm.doc.name !== name || frm.doc.docstatus !== 1) return;
		_payment_buttons(frm);
	});
}

function _payment_buttons(frm) {
	var d = frm.doc;
	var status = d.payment_status || "Unpaid";
	var group = __("Payment");
	var expired = d.payment_link_expires_on && frappe.datetime.str_to_obj(d.payment_link_expires_on) <= new Date();
	var active = !!d.razorpay_payment_link_url && status === "Link Sent" && !expired;

	// Partially Paid gets no new link either: it would ask for the full total again.
	if (status !== "Paid" && status !== "Partially Paid" && d.grand_total > 0) {
		if (active) {
			frm.add_custom_button(
				__("New Link"),
				function () {
					frappe.confirm(
						__("Cancel the current payment link and create a new one? The old link will stop working."),
						function () {
							_create_payment_link(frm, true);
						}
					);
				},
				group
			);
		} else {
			frm.add_custom_button(
				__("Create Payment Link"),
				function () {
					_create_payment_link(frm, false);
				},
				group
			);
		}
	}

	if (d.razorpay_payment_link_url) {
		frm.add_custom_button(
			__("Copy Link"),
			function () {
				frappe.utils.copy_to_clipboard(d.razorpay_payment_link_url);
			},
			group
		);
	}

	if (active) {
		frm.add_custom_button(
			__("Share on WhatsApp"),
			function () {
				_share_on_whatsapp(frm);
			},
			group
		);
	}

	if (d.razorpay_payment_link_id) {
		frm.add_custom_button(
			__("Refresh Payment Status"),
			function () {
				frappe
					.call({
						method: "field_sales_woocommerce.payments.razorpay.refresh_payment_status",
						args: { field_order: d.name },
						freeze: true,
						freeze_message: __("Checking Razorpay…"),
					})
					.then(function (r) {
						var paid = r.message && r.message.payment_status;
						frappe.show_alert({ message: __("Payment status: {0}", [__(paid)]), indicator: "blue" });
						frm.reload_doc();
					});
			},
			group
		);
	}
}

function _create_payment_link(frm, replace) {
	frappe
		.call({
			method: "field_sales_woocommerce.payments.razorpay.create_link_for_order",
			args: { field_order: frm.doc.name, replace: replace ? 1 : 0 },
			freeze: true,
			freeze_message: __("Creating payment link…"),
		})
		.then(function () {
			frappe.show_alert({ message: __("Payment link created"), indicator: "green" });
		})
		.always(function () {
			// Reload either way: a failed retry can still mark the old link cancelled.
			frm.reload_doc();
		});
}

function _share_on_whatsapp(frm) {
	var d = frm.doc;
	// The server reads the number for the retailer's country (frontend_api.get_order_payment_info).
	frappe.xcall("field_sales_woocommerce.frontend_api.get_order_payment_info", { field_order: d.name }, "GET").then(function (info) {
		var text = __("Hello {0}, please pay {1} for order {2} using this link: {3}", [
			d.retailer_name || d.retailer,
			format_currency(d.grand_total, frappe.defaults.get_default("currency")),
			d.store_order_name || d.name,
			d.razorpay_payment_link_url,
		]);
		var phone = info.whatsapp_number || "";
		if (!phone) {
			frappe.show_alert({
				message: __("The retailer has no valid mobile number; pick the contact in WhatsApp."),
				indicator: "orange",
			});
		}
		window.open("https://wa.me/" + phone + "?text=" + encodeURIComponent(text), "_blank", "noopener");
	});
}

// Indian mobile for wa.me: digits only with the 91 prefix (same rule as razorpay.indian_phone).

function _show_sync_status(frm) {
	if (frm.doc.docstatus !== 1) return;

	if (frm.doc.sync_status === "Failed") {
		frm.add_custom_button(__("Retry Sync"), function () {
			frappe
				.call({
					method: "field_sales_woocommerce.api.retry_sync",
					args: { field_order: frm.doc.name },
					freeze: true,
				})
				.then(function (r) {
					frappe.show_alert({ message: r.message.message, indicator: "blue" });
					frm.reload_doc();
				});
		}).addClass("btn-primary");

		frm.dashboard.set_headline(
			"<div>" +
				__("Store sync failed") +
				(frm.doc.sync_error ? ": " + frappe.utils.escape_html(frm.doc.sync_error) : "") +
				"</div>",
			"red"
		);
	} else if (frm.doc.sync_status === "Pending") {
		frm.dashboard.set_headline(__("Sending to the store…"), "orange");
	} else if (frm.doc.sync_status === "Synced") {
		frappe
			.call({
				method: "field_sales_woocommerce.api.get_store_order_url",
				args: { field_order: frm.doc.name },
			})
			.then(function (r) {
				if (!r.message) return;
				var label = __("Open in {0}", [frm.doc.platform || __("Store")]);
				frm.dashboard.set_headline(
					__("Synced to the store.") +
						' <a href="' +
						encodeURI(r.message) +
						'" target="_blank" rel="noopener noreferrer">' +
						label +
						"</a>",
					"green"
				);
			});
	}
}

// Reload an open order when its background push finishes.
function _listen_for_sync(frm) {
	if (frappe._field_order_sync_listener) return;
	frappe._field_order_sync_listener = true;

	function reload_if_open(data) {
		var open = window.cur_frm;
		if (open && open.doctype === "Field Order" && open.doc.name === data.name && !open.is_dirty()) {
			open.reload_doc();
		}
	}
	frappe.realtime.on("field_order_synced", reload_if_open);
	// Razorpay webhook / refresh changed the payment fields.
	frappe.realtime.on("field_order_payment_updated", reload_if_open);
}

// Offer the Store Connection's "Sell from here" locations in the Location select.
function _set_location_options(frm) {
	frappe.call({ method: "field_sales_woocommerce.api.get_sell_locations" }).then(function (r) {
		var options = (r.message || []).map(function (loc) {
			return { label: loc.location_name, value: loc.location_id };
		});
		// An order made at a location that is no longer sold from keeps showing it.
		if (frm.doc.location_id && !options.some(function (o) { return o.value === frm.doc.location_id; })) {
			options.push({ label: frm.doc.location_name || frm.doc.location_id, value: frm.doc.location_id });
		}
		frm.set_df_property("location_id", "options", [{ label: "", value: "" }].concat(options));
		frm.refresh_field("location_id");
	});
}

// ---------------------------------------------------------------------------
// Summary bar
// ---------------------------------------------------------------------------

function _show_order_summary(frm) {
	if (frm.is_new()) return;
	const items = frm.doc.items || [];
	const short = items.filter((row) => row.stock_warning).length;

	frm.dashboard.add_indicator(
		__("{0} items · {1} units", [items.length, format_number(frm.doc.total_qty || 0, null, 0)]),
		"blue"
	);
	frm.dashboard.add_indicator(__("Total {0}", [format_currency(frm.doc.grand_total)]), "green");
	if (frm.doc.docstatus === 0 && short) {
		frm.dashboard.add_indicator(__("{0} row(s) over stock", [short]), "red");
	}
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

// Only managers may set a rate other than the product's price; the server resets it for others.
function _lock_rate(frm) {
	var may_override = frappe.user.has_role(["Sales Manager", "System Manager"]);
	frm.fields_dict.items.grid.update_docfield_property("rate", "read_only", may_override ? 0 : 1);
}

// Show the rep's discount limit under Discount %.
function _show_discount_limit(frm) {
	if (frappe.user.has_role(["Sales Manager", "System Manager"])) {
		frm.set_df_property("discount_percent", "description", __("Managers are not limited"));
		return;
	}
	if (!frm.doc.sales_rep) {
		frm.set_df_property("discount_percent", "description", "");
		return;
	}
	frappe.db.get_value("Sales Rep", frm.doc.sales_rep, "max_discount_pct").then(function (r) {
		var max = flt((r.message || {}).max_discount_pct);
		frm.set_df_property(
			"discount_percent",
			"description",
			max > 0 ? __("Max {0}% for this rep", [max]) : __("This rep cannot give a discount")
		);
	});
}

// Sales Rep for an order without a visit: a rep takes it under their own name
// (they can order for any retailer); a manager gets the retailer's rep.
function _set_rep_from_retailer(frm) {
	var is_manager = frappe.user.has_role(["Sales Manager", "System Manager"]);
	var own_rep = is_manager
		? Promise.resolve(null)
		: frappe.db.get_value("Sales Rep", { user: frappe.session.user, active: 1 }, "name").then(function (r) {
				return r.message && r.message.name;
		  });
	own_rep.then(function (rep) {
		if (rep) {
			frm.set_value("sales_rep", rep);
			return;
		}
		frappe.db.get_value("Retailer", frm.doc.retailer, "sales_rep", function (r) {
			if (r && r.sales_rep) {
				frm.set_value("sales_rep", r.sales_rep);
			}
		});
	});
}

/**
 * Recompute amount for a single child row, then roll up parent totals.
 */
function _recalc_row(frm, cdt, cdn) {
	var row = locals[cdt][cdn];
	var amount = flt(row.qty) * flt(row.rate);
	frappe.model.set_value(cdt, cdn, "amount", amount).then(function () {
		_recalc_totals(frm);
		_highlight_stock_warnings(frm);
	});
}

/**
 * Recompute order-level totals in the browser so the user sees live values
 * without needing to save.
 */
function _recalc_totals(frm) {
	var total_qty = 0;
	var net_total = 0;

	(frm.doc.items || []).forEach(function (row) {
		total_qty += flt(row.qty);
		net_total += flt(row.amount);
	});

	// Rounded like the server does, so the form shows what will be saved.
	net_total = flt(net_total, precision("net_total"));
	var tax_amount = flt(net_total * flt(frm.doc.tax_percent) / 100, precision("tax_amount"));
	var discount_amount = flt(net_total * flt(frm.doc.discount_percent) / 100, precision("discount_amount"));
	var grand_total = flt(
		net_total + tax_amount - discount_amount,
		precision("grand_total")
	);

	frm.set_value("total_qty", total_qty);
	frm.set_value("net_total", net_total);
	frm.set_value("tax_amount", tax_amount);
	frm.set_value("discount_amount", discount_amount);
	frm.set_value("grand_total", grand_total);
}

/**
 * Paint child rows red when stock_warning is set, clear it otherwise.
 * Called after every qty/rate change and on form refresh.
 */
function _highlight_stock_warnings(frm) {
	var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;

	grid.grid_rows.forEach(function (grid_row) {
		var doc = grid_row.doc;
		if (doc && doc.stock_warning) {
			$(grid_row.row).css({
				"background-color": "#fde8e8",
				"color": "#c0392b",
			});
		} else {
			$(grid_row.row).css({
				"background-color": "",
				"color": "",
			});
		}
	});
}
