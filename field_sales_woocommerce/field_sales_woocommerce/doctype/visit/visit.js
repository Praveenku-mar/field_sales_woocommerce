// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

const DEFAULT_GEOFENCE_RADIUS_M = 50;

const GPS_OPTIONS = { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 };

frappe.ui.form.on("Visit", {
	onload(frm) {
		if (frm.is_new()) {
			set_new_visit_defaults(frm);
		}
	},

	refresh(frm) {
		add_check_in_buttons(frm);
		show_visit_summary(frm);
		show_geofence_indicator(frm);
		render_location_map(frm);
		render_orders(frm);
	},
});

// ----------------------------------------------------------------------
// New visit defaults (also used by the workspace "New Visit" shortcut)
// ----------------------------------------------------------------------

function set_new_visit_defaults(frm) {
	if (!frm.doc.visit_date) {
		frm.set_value("visit_date", frappe.datetime.get_today());
	}
	if (frm.doc.sales_rep) return;

	frappe.db
		.get_value("Sales Rep", { user: frappe.session.user, active: 1 }, "name")
		.then(({ message }) => {
			// The user may have picked a rep while the lookup was running.
			if (message && message.name && !frm.doc.sales_rep) {
				frm.set_value("sales_rep", message.name);
			}
		});
}

// ----------------------------------------------------------------------
// Check In / Check Out
// ----------------------------------------------------------------------

function add_check_in_buttons(frm) {
	if (frm.is_new()) return;

	// Only the assigned rep checks in (the server enforces it too). A Sales Rep is
	// named after its user, so the rep's name is the user's email.
	if (frm.doc.status === "Planned" && frm.doc.sales_rep === frappe.session.user) {
		frm.add_custom_button(__("Check In"), () =>
			check_in_or_out(frm, "field_sales_woocommerce.api.check_in", __("Checking in…"))
		).addClass("btn-primary");
	}

	if (frm.doc.status === "In Progress") {
		// Not returning the promise: the button would spin while the reason dialog is open.
		frm.add_custom_button(__("Check Out"), () => {
			get_no_order_reason(frm).then((reason) =>
				check_in_or_out(frm, "field_sales_woocommerce.api.check_out", __("Checking out…"), reason)
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("New Field Order"), () => new_field_order(frm));
	}
}

function new_field_order(frm) {
	frappe.new_doc("Field Order", {
		retailer: frm.doc.retailer,
		visit: frm.doc.name,
		sales_rep: frm.doc.sales_rep,
	});
}

/**
 * Resolves with {} when the visit has an order, otherwise asks why no order
 * was taken and resolves with the answer (never resolves if the rep cancels).
 */
function get_no_order_reason(frm) {
	return frappe.db.count("Field Order", { filters: { visit: frm.doc.name, docstatus: ["<", 2] } }).then((count) => {
		if (count) return {};

		return new Promise((resolve) => {
			const dialog = new frappe.ui.Dialog({
				title: __("No Order Taken"),
				fields: [
					{
						fieldtype: "HTML",
						options: `<p class="text-muted">${__(
							"No order was taken on this visit. Tell us why before checking out."
						)}</p>`,
					},
					{
						fieldname: "no_order_reason",
						fieldtype: "Select",
						label: __("Reason"),
						options: frm.get_field("no_order_reason").df.options,
						default: frm.doc.no_order_reason,
						reqd: 1,
					},
					{
						fieldname: "no_order_remarks",
						fieldtype: "Small Text",
						label: __("Details"),
						default: frm.doc.no_order_remarks,
						mandatory_depends_on: "eval:doc.no_order_reason=='Other'",
					},
				],
				primary_action_label: __("Check Out"),
				primary_action(values) {
					dialog.hide();
					resolve(values);
				},
			});
			dialog.show();
		});
	});
}

function check_in_or_out(frm, method, freeze_message, extra_args = {}) {
	// Save pending edits (e.g. notes) first, so reload_doc does not discard them.
	const saved = frm.is_dirty() ? frm.save() : Promise.resolve();

	saved
		.then(() => get_current_position())
		.then((coords) =>
			frappe.call({
				method,
				args: {
					visit: frm.doc.name,
					latitude: coords.latitude,
					longitude: coords.longitude,
					accuracy: coords.accuracy,
					...extra_args,
				},
				freeze: true,
				freeze_message,
			})
		)
		.then(({ message: r }) => {
			if (r.warning) {
				frappe.msgprint({ title: __("Outside Geofence"), message: r.warning, indicator: "orange" });
			} else if (r.retailer_location_set) {
				frappe.show_alert({
					message: __("Retailer location saved from this check-in"),
					indicator: "green",
				});
			} else {
				frappe.show_alert({ message: __("Visit {0}", [__(r.status)]), indicator: "green" });
			}
			return frm.reload_doc();
		})
		.catch((err) => {
			// Server errors are already shown by frappe.call; only report location failures here.
			if (err && err.is_location_error) {
				frappe.msgprint({ title: __("Location Unavailable"), message: err.message, indicator: "red" });
			}
		});
}

function get_current_position() {
	return new Promise((resolve, reject) => {
		const fail = (message) => reject({ is_location_error: true, message });
		const enable_msg = __("Allow location access in your browser and use HTTPS.");

		if (!window.isSecureContext || !navigator.geolocation) {
			return fail(__("Location is not available on this page.") + " " + enable_msg);
		}

		frappe.dom.freeze(__("Getting your location…"));
		navigator.geolocation.getCurrentPosition(
			(position) => {
				frappe.dom.unfreeze();
				resolve(position.coords);
			},
			(error) => {
				frappe.dom.unfreeze();
				if (error.code === error.PERMISSION_DENIED) {
					fail(__("Location permission was denied.") + " " + enable_msg);
				} else if (error.code === error.TIMEOUT) {
					fail(__("Could not get a GPS fix within 15 seconds. Move to an open area and try again."));
				} else {
					fail(__("Your location is unavailable.") + " " + enable_msg);
				}
			},
			GPS_OPTIONS
		);
	});
}

// ----------------------------------------------------------------------
// Geofence indicator and map
// ----------------------------------------------------------------------

function has_coords(latitude, longitude) {
	// (0, 0) is treated as "not set": it is never a real location.
	return Boolean(flt(latitude) || flt(longitude));
}

function get_geofence_radius(frm) {
	return frappe.db
		.get_value("Retailer", frm.doc.retailer, "geofence_radius_m")
		.then(({ message }) => (message && cint(message.geofence_radius_m)) || DEFAULT_GEOFENCE_RADIUS_M);
}

function show_geofence_indicator(frm) {
	if (!frm.doc.check_in_time || !has_coords(frm.doc.retailer_latitude, frm.doc.retailer_longitude)) {
		return;
	}

	get_geofence_radius(frm).then((radius) => {
		if (frm.doc.within_geofence) {
			frm.dashboard.add_indicator(__("Within {0} m", [radius]), "green");
		} else {
			frm.dashboard.add_indicator(
				__("{0} m away", [Math.round(flt(frm.doc.distance_from_retailer_m))]),
				"orange"
			);
		}
	});
}

function render_location_map(frm) {
	const field = frm.get_field("location_map_html");
	if (frm._visit_map) {
		frm._visit_map.remove();
		frm._visit_map = null;
	}
	field.$wrapper.empty();

	const has_check_in = has_coords(frm.doc.latitude, frm.doc.longitude);
	const has_retailer = has_coords(frm.doc.retailer_latitude, frm.doc.retailer_longitude);
	if (!frm.doc.check_in_time || !(has_check_in || has_retailer)) return;

	const container = $(`<div style="height: 300px; border-radius: var(--border-radius-md);"></div>`).appendTo(
		field.$wrapper
	);

	Promise.all([
		frappe.require("leaflet.bundle.js"),
		frappe.require("leaflet.bundle.css"),
		has_retailer ? get_geofence_radius(frm) : null,
	]).then(([, , radius]) => {
		// The form may have been refreshed again while Leaflet loaded.
		if (!document.body.contains(container[0])) return;

		const defaults = frappe.utils.map_defaults;
		L.Icon.Default.imagePath = defaults.image_path;

		const map = L.map(container[0], { scrollWheelZoom: false });
		L.tileLayer(defaults.tiles.default_tile.url, defaults.tiles.default_tile.options).addTo(map);
		frm._visit_map = map;

		const points = [];
		if (has_retailer) {
			const shop = [flt(frm.doc.retailer_latitude), flt(frm.doc.retailer_longitude)];
			L.marker(shop).addTo(map).bindTooltip(__("Retailer: {0}", [frm.doc.retailer_name || frm.doc.retailer]));
			L.circle(shop, { radius, color: "#2490ef", weight: 1, fillOpacity: 0.08 }).addTo(map);
			points.push(shop);
		}
		if (has_check_in) {
			const here = [flt(frm.doc.latitude), flt(frm.doc.longitude)];
			L.circleMarker(here, {
				radius: 8,
				color: "#fff",
				weight: 2,
				fillColor: frm.doc.within_geofence ? "#29cd42" : "#f8814f",
				fillOpacity: 1,
			})
				.addTo(map)
				.bindTooltip(__("Check-in (±{0} m)", [Math.round(flt(frm.doc.location_accuracy_m))]));
			points.push(here);
		}

		if (points.length > 1) {
			map.fitBounds(points, { padding: [40, 40], maxZoom: 18 });
		} else {
			map.setView(points[0], 17);
		}
		// The section may have just become visible; let Leaflet measure it.
		setTimeout(() => map.invalidateSize(), 100);
	});
}

// ----------------------------------------------------------------------
// Orders taken on this visit (drafts included, so an order waiting on stock shows)
// ----------------------------------------------------------------------

const ORDER_STATUS_COLORS = {
	Draft: "gray",
	"Out of Stock": "red",
	Pending: "orange",
	Synced: "green",
	Failed: "red",
	Cancelled: "gray",
};

function render_orders(frm) {
	const field = frm.get_field("orders_html");
	if (!field) return;
	field.$wrapper.empty();
	if (frm.is_new()) return;

	frappe
		.call({ method: "field_sales_woocommerce.api.get_visit_orders", args: { visit: frm.doc.name } })
		.then(({ message: orders = [] }) => {
			field.$wrapper.empty();
			const can_add = ["In Progress", "Completed"].includes(frm.doc.status) && frappe.model.can_create("Field Order");

			if (!orders.length) {
				field.$wrapper.append(
					`<div class="text-muted" style="margin-bottom: var(--margin-sm)">${__("No order taken on this visit yet.")}</div>`
				);
			} else {
				const rows = orders
					.map((o) => {
						const note =
							o.status === "Out of Stock"
								? __("{0} item(s) exceed available stock", [o.short_items])
								: o.status === "Failed" && o.sync_error
								? frappe.utils.escape_html(o.sync_error)
								: "";
						return `<tr>
							<td><a href="/app/field-order/${encodeURIComponent(o.name)}">${o.name}</a></td>
							<td>${frappe.datetime.str_to_user(o.order_date)}</td>
							<td class="text-right">${__("{0} items · {1} units", [o.items, format_number(o.total_qty, null, 0)])}</td>
							<td class="text-right">${format_currency(o.grand_total)}</td>
							<td><span class="indicator-pill ${ORDER_STATUS_COLORS[o.status] || "gray"}">${__(o.status)}</span>
								${note ? `<div class="small text-muted" style="margin-top: 4px">${note}</div>` : ""}</td>
						</tr>`;
					})
					.join("");
				field.$wrapper.append(`<table class="table table-bordered" style="margin-bottom: var(--margin-sm)">
					<thead><tr>
						<th>${__("Order")}</th><th>${__("Date")}</th><th class="text-right">${__("Items")}</th>
						<th class="text-right">${__("Total")}</th><th>${__("Status")}</th>
					</tr></thead><tbody>${rows}</tbody></table>`);
			}

			if (can_add) {
				$(`<button class="btn btn-xs btn-default">${__("New Field Order")}</button>`)
					.on("click", () => new_field_order(frm))
					.appendTo(field.$wrapper);
			}
		});
}

// ----------------------------------------------------------------------
// Summary bar: where the visit stands
// ----------------------------------------------------------------------

function show_visit_summary(frm) {
	if (frm.is_new()) return;
	const d = frm.doc;

	if (d.status === "In Progress" && d.check_in_time) {
		const minutes = Math.max(0, Math.round(moment().diff(moment(d.check_in_time), "minutes")));
		frm.dashboard.set_headline(
			__("Checked in at {0} · {1} min on site. Check out when you leave.", [
				frappe.datetime.str_to_user(d.check_in_time).split(" ").slice(-1)[0],
				minutes,
			]),
			"blue"
		);
	} else if (d.status === "Planned") {
		const overdue = d.visit_date < frappe.datetime.get_today();
		frm.dashboard.set_headline(
			overdue
				? __("This visit was planned for {0} and has not been checked in.", [frappe.datetime.str_to_user(d.visit_date)])
				: __("Check in when you reach the retailer."),
			overdue ? "orange" : "gray"
		);
	} else {
		frm.dashboard.clear_headline();
	}

	if (d.status === "Completed") {
		frm.dashboard.add_indicator(__("{0} min on site", [d.time_on_site_min || 0]), "blue");
		frm.dashboard.add_indicator(
			d.order_taken ? __("Order taken") : d.no_order_reason ? __("No order: {0}", [__(d.no_order_reason)]) : __("No order"),
			d.order_taken ? "green" : "gray"
		);
	}
}
