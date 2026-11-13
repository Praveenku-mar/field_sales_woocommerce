# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""
	Backfill client_temp_key for active Field Orders (docstatus 0/1) and Visits
	(status != 'Cancelled'), one per client_temp_id. The oldest active row gets
	the key; existing duplicates keep NULL so the unique index cannot fail.
	Safe to re-run.
	"""
	if frappe.db.has_column("Field Order", "client_temp_key"):
		# Clear key for inactive rows (docstatus 2) or rows without client_temp_id
		frappe.db.sql(
			"""
			UPDATE `tabField Order`
			SET client_temp_key = NULL
			WHERE (docstatus = 2 OR client_temp_id IS NULL OR client_temp_id = '')
			  AND client_temp_key IS NOT NULL
			"""
		)
		# Clear key for active duplicate rows (newer than the oldest active row)
		frappe.db.sql(
			"""
			UPDATE `tabField Order` o
			JOIN (
				SELECT name
				FROM (
					SELECT name, ROW_NUMBER() OVER (
						PARTITION BY client_temp_id
						ORDER BY creation ASC, name ASC
					) AS rn
					FROM `tabField Order`
					WHERE docstatus IN (0, 1)
					  AND client_temp_id IS NOT NULL
					  AND client_temp_id != ''
				) ranked
				WHERE ranked.rn > 1
			) dupes ON o.name = dupes.name
			SET o.client_temp_key = NULL
			WHERE o.client_temp_key IS NOT NULL
			"""
		)
		# Fill key for the oldest active row per client_temp_id
		frappe.db.sql(
			"""
			UPDATE `tabField Order` o
			JOIN (
				SELECT name
				FROM (
					SELECT name, ROW_NUMBER() OVER (
						PARTITION BY client_temp_id
						ORDER BY creation ASC, name ASC
					) AS rn
					FROM `tabField Order`
					WHERE docstatus IN (0, 1)
					  AND client_temp_id IS NOT NULL
					  AND client_temp_id != ''
				) ranked
				WHERE ranked.rn = 1
			) oldest ON o.name = oldest.name
			SET o.client_temp_key = o.client_temp_id
			WHERE o.client_temp_key IS NULL OR o.client_temp_key != o.client_temp_id
			"""
		)

	if frappe.db.has_column("Visit", "client_temp_key"):
		# Clear key for inactive rows (status == 'Cancelled') or rows without client_temp_id
		frappe.db.sql(
			"""
			UPDATE `tabVisit`
			SET client_temp_key = NULL
			WHERE (status = 'Cancelled' OR client_temp_id IS NULL OR client_temp_id = '')
			  AND client_temp_key IS NOT NULL
			"""
		)
		# Clear key for active duplicate rows (newer than the oldest active row)
		frappe.db.sql(
			"""
			UPDATE `tabVisit` v
			JOIN (
				SELECT name
				FROM (
					SELECT name, ROW_NUMBER() OVER (
						PARTITION BY client_temp_id
						ORDER BY creation ASC, name ASC
					) AS rn
					FROM `tabVisit`
					WHERE status != 'Cancelled'
					  AND client_temp_id IS NOT NULL
					  AND client_temp_id != ''
				) ranked
				WHERE ranked.rn > 1
			) dupes ON v.name = dupes.name
			SET v.client_temp_key = NULL
			WHERE v.client_temp_key IS NOT NULL
			"""
		)
		# Fill key for the oldest active row per client_temp_id
		frappe.db.sql(
			"""
			UPDATE `tabVisit` v
			JOIN (
				SELECT name
				FROM (
					SELECT name, ROW_NUMBER() OVER (
						PARTITION BY client_temp_id
						ORDER BY creation ASC, name ASC
					) AS rn
					FROM `tabVisit`
					WHERE status != 'Cancelled'
					  AND client_temp_id IS NOT NULL
					  AND client_temp_id != ''
				) ranked
				WHERE ranked.rn = 1
			) oldest ON v.name = oldest.name
			SET v.client_temp_key = v.client_temp_id
			WHERE v.client_temp_key IS NULL OR v.client_temp_key != v.client_temp_id
			"""
		)
