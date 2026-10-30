# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Geographic helpers shared by Visit check-in / check-out."""

import json
import math

from frappe.utils import flt

EARTH_RADIUS_M = 6_371_000  # mean Earth radius in metres
DEFAULT_GEOFENCE_RADIUS_M = 50


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
	"""
	Return the great-circle distance in **metres** between two GPS points.

	    a = sin²(Δφ/2) + cos(φ1)·cos(φ2)·sin²(Δλ/2)
	    d = 2R · atan2(√a, √(1−a))

	where φ = latitude, λ = longitude, R = mean Earth radius.
	"""
	phi1 = math.radians(lat1)
	phi2 = math.radians(lat2)
	d_phi = math.radians(lat2 - lat1)
	d_lam = math.radians(lon2 - lon1)

	a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
	return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def has_coords(latitude, longitude) -> bool:
	"""Missing values or (0, 0) count as "no location": it is never a real shop."""
	return bool(flt(latitude) or flt(longitude))


def is_valid_coords(latitude: float, longitude: float) -> bool:
	return -90 <= latitude <= 90 and -180 <= longitude <= 180


def point_geojson(latitude: float, longitude: float) -> str:
	"""A single-pin FeatureCollection, the format Frappe's Geolocation field stores."""
	return json.dumps(
		{
			"type": "FeatureCollection",
			"features": [
				{
					"type": "Feature",
					"properties": {},
					"geometry": {"type": "Point", "coordinates": [longitude, latitude]},
				}
			],
		}
	)
