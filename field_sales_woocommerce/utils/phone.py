# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

"""
Phone numbers for any country, with the ``phonenumbers`` library (a Frappe dependency).

``country_code`` is an ISO 3166-1 alpha-2 code (``Country.code``, any case). A number
written with ``+`` and a country prefix is read as such whatever ``country_code`` says.
"""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType


def _parse(phone: str | None, country_code: str | None):
	text = (phone or "").strip()
	if not text:
		return None
	try:
		return phonenumbers.parse(text, (country_code or "").upper() or None)
	except NumberParseException:
		return None


def is_valid(phone: str | None, country_code: str | None) -> bool:
	"""True if ``phone`` is a valid number for its country (``country_code`` unless it has a +prefix)."""
	number = _parse(phone, country_code)
	return bool(number) and phonenumbers.is_valid_number(number)


def to_e164(phone: str | None, country_code: str | None) -> str | None:
	"""``"98765 43210", "IN"`` -> ``"+919876543210"``; ``None`` if the number is not valid."""
	number = _parse(phone, country_code)
	if not number or not phonenumbers.is_valid_number(number):
		return None
	return phonenumbers.format_number(number, PhoneNumberFormat.E164)


def whatsapp_digits(phone: str | None, country_code: str | None) -> str | None:
	"""
	Digits for a ``wa.me/<digits>`` link (country prefix, no ``+``), or ``None``.

	Only for numbers that can be mobiles: a landline gives ``None`` (WhatsApp can't reach it).
	"""
	number = _parse(phone, country_code)
	if not number or not phonenumbers.is_valid_number(number):
		return None
	if phonenumbers.number_type(number) not in (
		PhoneNumberType.MOBILE,
		PhoneNumberType.FIXED_LINE_OR_MOBILE,
	):
		return None
	return phonenumbers.format_number(number, PhoneNumberFormat.E164)[1:]


def example_number(country_code: str | None) -> str | None:
	"""An example mobile number for the country, as it is written there (for hints and errors)."""
	code = (country_code or "").upper()
	number = phonenumbers.example_number_for_type(code, PhoneNumberType.MOBILE) if code else None
	if not number:
		return None
	return phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
