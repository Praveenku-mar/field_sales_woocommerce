// Copyright (c) 2026, Your Company and contributors
// For license information, please see license.txt

const COORD_PRECISION = 6;

frappe.ui.form.on("Retailer", {
	onload(frm) {
		// New retailers start in the store's country (else System Settings').
		if (frm.is_new() && !frm.doc.country) {
			frappe
				.xcall("field_sales_woocommerce.field_sales_woocommerce.doctype.retailer.retailer.get_default_country", {}, "GET")
				.then((country) => country && !frm.doc.country && frm.set_value("country", country));
		}
	},

	refresh(frm) {
		lock_location(frm);
		add_use_my_location_button(frm);
		draw_pin_if_missing(frm);
		bind_map_locate(frm);

		if (frm.is_new()) return;

		show_retailer_summary(frm);
		frm.add_custom_button(__("Plan Visit"), () => frappe.new_doc("Visit", { retailer: frm.doc.name }), __("Create"));
		frm.add_custom_button(
			__("Field Order"),
			() => frappe.new_doc("Field Order", { retailer: frm.doc.name, sales_rep: frm.doc.sales_rep }),
			__("Create")
		);

		if (["Draft", "Failed"].includes(frm.doc.sync_status)) {
			frm.add_custom_button(__("Sync to Store"), () => {
				frappe
					.call({
						method: "field_sales_woocommerce.api.sync_retailers",
						args: { names: [frm.doc.name] },
						freeze: true,
					})
					.then(({ message: r }) => {
						frappe.show_alert({ message: r.message, indicator: "blue" });
						frm.reload_doc();
					});
			});
		}

		if (frm.doc.sync_status === "Failed" && frm.doc.sync_error) {
			frm.dashboard.set_headline(
				`<div>${__("Store sync failed")}: ${frappe.utils.escape_html(frm.doc.sync_error)}</div>`,
				"red"
			);
		} else if (frm.doc.sync_status === "Synced" && frm.doc.sync_error) {
			// Synced with a caveat, e.g. the store rejected the email.
			frm.dashboard.set_headline(`<div>${frappe.utils.escape_html(frm.doc.sync_error)}</div>`, "orange");
		} else {
			frm.dashboard.clear_headline();
		}
	},

	// The map lives in a tab: Leaflet measures a hidden tab as 0x0, so resize when shown.
	on_tab_change(frm) {
		const map = frm.fields_dict.location_map && frm.fields_dict.location_map.map;
		if (map) setTimeout(() => map.invalidateSize(), 50);
	},

	// Handlers return their promise so Frappe finishes one update before
	// applying the next value (e.g. latitude, then longitude).
	latitude(frm) {
		return sync_map_from_coords(frm);
	},

	longitude(frm) {
		return sync_map_from_coords(frm);
	},

	location_map(frm) {
		return sync_coords_from_map(frm);
	},
});

// ----------------------------------------------------------------------
// Map <-> latitude/longitude sync
// ----------------------------------------------------------------------

function has_coords(doc) {
	// (0, 0) is treated as "not set": it is never a real retailer location.
	return Boolean(flt(doc.latitude) || flt(doc.longitude));
}

function point_geojson(latitude, longitude) {
	return JSON.stringify({
		type: "FeatureCollection",
		features: [
			{
				type: "Feature",
				properties: {},
				geometry: { type: "Point", coordinates: [flt(longitude), flt(latitude)] },
			},
		],
	});
}

// The most recently added Point (marker or circle centre) in the map value.
// Leaflet Draw appends new pins, so the last one is the one the user just placed.
function latest_point(geojson) {
	let data;
	try {
		data = typeof geojson === "string" ? JSON.parse(geojson) : geojson;
	} catch (e) {
		return null;
	}

	const features = (data && data.features) || [];
	for (let i = features.length - 1; i >= 0; i--) {
		const geometry = features[i] && features[i].geometry;
		if (geometry && geometry.type === "Point" && Array.isArray(geometry.coordinates)) {
			const [longitude, latitude] = geometry.coordinates;
			return { latitude, longitude };
		}
	}
	return null;
}

// Runs `update` with the sync flag set, so the handlers it triggers do not
// echo the change back (latitude -> map -> latitude -> ...).
function while_syncing(frm, update) {
	if (frm._syncing_location) return Promise.resolve();
	frm._syncing_location = true;
	return Promise.resolve(update()).finally(() => {
		frm._syncing_location = false;
	});
}

function sync_map_from_coords(frm) {
	if (frm._syncing_location) return;

	if (!has_coords(frm.doc)) {
		return frm.doc.location_map ? while_syncing(frm, () => frm.set_value("location_map", "")) : null;
	}
	// Wait for both values while the user is still typing one of them.
	if (frm.doc.latitude == null || frm.doc.longitude == null) return;

	const value = point_geojson(frm.doc.latitude, frm.doc.longitude);
	if (value === frm.doc.location_map) return;

	return while_syncing(frm, () => frm.set_value("location_map", value));
}

function sync_coords_from_map(frm) {
	if (frm._syncing_location) return;

	const point = latest_point(frm.doc.location_map);
	if (!point) return;

	const latitude = flt(point.latitude, COORD_PRECISION);
	const longitude = flt(point.longitude, COORD_PRECISION);

	return while_syncing(frm, () =>
		frm.set_value({
			latitude,
			longitude,
			// Collapse to a single pin, so older pins do not linger on the map.
			location_map: point_geojson(latitude, longitude),
		})
	);
}

function set_coords(frm, latitude, longitude) {
	// Goes through the latitude/longitude handlers, which redraw the map.
	return frm.set_value({
		latitude: flt(latitude, COORD_PRECISION),
		longitude: flt(longitude, COORD_PRECISION),
	});
}

// Older retailers have coordinates but no map value: show their pin without
// marking the form as changed.
function draw_pin_if_missing(frm) {
	if (frm.doc.location_map || !has_coords(frm.doc)) return;
	frm.doc.location_map = point_geojson(frm.doc.latitude, frm.doc.longitude);
	frm.refresh_field("location_map");
}

// The map's own "locate me" control only pans the map; it does not change
// the field. Listen for the located position and use it as the retailer's.
function bind_map_locate(frm, attempts = 20) {
	const field = frm.fields_dict.location_map;
	if (!field) return;

	// The control builds its Leaflet map asynchronously after loading its library.
	if (!field.map) {
		if (attempts > 0) setTimeout(() => bind_map_locate(frm, attempts - 1), 250);
		return;
	}
	if (field.map._field_sales_locate_bound) return;
	field.map._field_sales_locate_bound = true;

	field.map.on("locationfound", (e) => {
		if (!frm.has_perm("write")) return;
		set_coords(frm, e.latlng.lat, e.latlng.lng);
	});
}

// ----------------------------------------------------------------------
// "Use My Location"
// ----------------------------------------------------------------------

// Matches Retailer._guard_location: reps set a retailer's first location; after that only
// managers move it, and only managers change the geofence radius.
function location_locked(frm) {
	return !is_location_editor() && !frm.is_new() && has_coords(frm.doc);
}

function is_location_editor() {
	return frappe.user.has_role(["Sales Manager", "System Manager"]);
}

function lock_location(frm) {
	const locked = location_locked(frm) ? 1 : 0;
	["latitude", "longitude", "location_map"].forEach((f) => frm.set_df_property(f, "read_only", locked));
	frm.set_df_property("geofence_radius_m", "read_only", is_location_editor() ? 0 : 1);
}

function add_use_my_location_button(frm) {
	if (!frm.has_perm("write") || location_locked(frm)) return;

	frm.add_custom_button(__("Use My Location"), () => {
		if (!navigator.geolocation) {
			frappe.msgprint(__("This browser does not support location access."));
			return;
		}

		frappe.show_alert({ message: __("Getting your location..."), indicator: "blue" });
		navigator.geolocation.getCurrentPosition(
			(position) => {
				const { latitude, longitude, accuracy } = position.coords;
				set_coords(frm, latitude, longitude).then(() => {
					frappe.show_alert({
						message: __("Location set (accuracy about {0} m)", [Math.round(accuracy)]),
						indicator: "green",
					});
				});
			},
			(error) => {
				const messages = {
					[error.PERMISSION_DENIED]: __(
						"Location permission was denied. Allow location access for this site in your browser settings."
					),
					[error.POSITION_UNAVAILABLE]: __("Your location could not be determined."),
					[error.TIMEOUT]: __("Timed out while getting your location. Please try again."),
				};
				frappe.msgprint({
					title: __("Location Unavailable"),
					indicator: "red",
					message: messages[error.code] || error.message,
				});
			},
			{ enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
		);
	});
}

// ----------------------------------------------------------------------
// Summary bar: last visit and the last 30 days
// ----------------------------------------------------------------------

function show_retailer_summary(frm) {
	const last = frm.doc.last_visit;
	const days = last ? frappe.datetime.get_day_diff(frappe.datetime.get_today(), last) : null;
	const color = days === null || days > 30 ? "red" : days > 7 ? "orange" : "green";
	frm.dashboard.add_indicator(
		last ? __("Last visit {0}", [frappe.datetime.prettyDate(last)]) : __("Never visited"),
		color
	);

	frappe
		.call({ method: "field_sales_woocommerce.api.get_retailer_summary", args: { retailer: frm.doc.name } })
		.then(({ message: s }) => {
			if (!s || frm.doc.name !== frm.docname) return;
			frm.dashboard.add_indicator(__("{0} visits in 30 days", [s.visits_30d]), "blue");
			frm.dashboard.add_indicator(
				__("{0} orders · {1} in 30 days", [s.orders_30d, format_currency(s.value_30d)]),
				s.orders_30d ? "green" : "gray"
			);
		});
}
