# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""Helpers for user-typed search terms."""


def escape_like(term: str) -> str:
	"""
	Escape LIKE wildcards in a search term, so "%" or "_" match themselves and a search
	for "%%" does not match every row. Uses the default escape character (backslash).
	"""
	return (term or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
