from collections.abc import Mapping

import phonenumbers
from rest_framework import serializers


COUNTRY_TO_REGION = {
    "AFGHANISTAN": "AF",
    "ARGENTINA": "AR",
    "AUSTRALIA": "AU",
    "AUSTRIA": "AT",
    "BANGLADESH": "BD",
    "BELGIUM": "BE",
    "BRAZIL": "BR",
    "CANADA": "CA",
    "CHINA": "CN",
    "DENMARK": "DK",
    "EGYPT": "EG",
    "FINLAND": "FI",
    "FRANCE": "FR",
    "GERMANY": "DE",
    "GREECE": "GR",
    "HONG KONG": "HK",
    "INDIA": "IN",
    "INDONESIA": "ID",
    "IRELAND": "IE",
    "ISRAEL": "IL",
    "ITALY": "IT",
    "JAPAN": "JP",
    "KENYA": "KE",
    "MALAYSIA": "MY",
    "MEXICO": "MX",
    "NEPAL": "NP",
    "NETHERLANDS": "NL",
    "THE NETHERLANDS": "NL",
    "NEW ZEALAND": "NZ",
    "NIGERIA": "NG",
    "NORWAY": "NO",
    "PAKISTAN": "PK",
    "PHILIPPINES": "PH",
    "POLAND": "PL",
    "PORTUGAL": "PT",
    "QATAR": "QA",
    "RUSSIA": "RU",
    "SAUDI ARABIA": "SA",
    "SINGAPORE": "SG",
    "SOUTH AFRICA": "ZA",
    "SOUTH KOREA": "KR",
    "SPAIN": "ES",
    "SRI LANKA": "LK",
    "SWEDEN": "SE",
    "SWITZERLAND": "CH",
    "TAIWAN": "TW",
    "THAILAND": "TH",
    "TURKEY": "TR",
    "UAE": "AE",
    "UNITED ARAB EMIRATES": "AE",
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "UNITED KINGDOM": "GB",
    "US": "US",
    "USA": "US",
    "U.S.": "US",
    "U.S.A.": "US",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "VIETNAM": "VN",
}


def resolve_region_from_country(country):
    if not isinstance(country, str):
        return None

    key = " ".join(country.strip().split()).upper()
    if not key:
        return None

    if key in COUNTRY_TO_REGION:
        return COUNTRY_TO_REGION[key]

    if (
        len(key) == 2
        and key.isalpha()
        and key in phonenumbers.SUPPORTED_REGIONS
    ):
        return key

    return None


def normalize_phone_number(value, country=None):
    if not isinstance(value, str):
        raise serializers.ValidationError(
            "Enter a valid phone number."
        )

    value = value.strip()

    if not value:
        return ""

    if not value.startswith("+"):
        raise serializers.ValidationError(
            "Phone number must include an international "
            "country code, such as +91 or +1."
        )

    try:
        parsed_number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException:
        raise serializers.ValidationError(
            "Enter a valid international phone number."
        )

    if not phonenumbers.is_valid_number(parsed_number):
        raise serializers.ValidationError(
            "Enter a valid international phone number."
        )

    return phonenumbers.format_number(
        parsed_number,
        phonenumbers.PhoneNumberFormat.E164,
    )


def validate_phone_number(value, country=None):
    return normalize_phone_number(value, country=country)


def validate_phone_for_country(value, country):
    """
    Validate an international phone number independently
    of the company's registered country.
    The country argument is retained for compatibility
    with existing service calls.
    """
    if not isinstance(value, str) or not value.strip():
        raise serializers.ValidationError(
            "Phone number is required."
        )
    return normalize_phone_number(value)


class PhoneNumberField(serializers.CharField):
    def __init__(self, *args, country_field=None, **kwargs):
        self.country_field = country_field
        super().__init__(*args, **kwargs)

    def _get_country_value(self):
        if self.parent is None:
            return None

        # 1. Use country provided in the current request.
        if self.country_field:
            for attr in ("_raw_data", "initial_data"):
                raw = getattr(self.parent, attr, None)

                if (
                    isinstance(raw, Mapping)
                    and self.country_field in raw
                ):
                    return raw.get(self.country_field)

        # 2. Otherwise use the existing instance.
        instance = getattr(self.parent, "instance", None)

        if instance is None:
            return None

        # 3. Customer model: use its own country.
        if hasattr(instance, "country"):
            return instance.country

        # 4. User model: use associated customer company country.
        from apps.accounts.models import User
        from apps.customers.models import CustomerUser

        if isinstance(instance, User):
            membership = (
                CustomerUser.objects
                .select_related("customer")
                .filter(user=instance)
                .first()
            )

            if membership:
                return membership.customer.country

        return None

    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError(
                "Enter a valid phone number."
            )

        country = self._get_country_value()
        normalized = normalize_phone_number(data, country=country)
        return super().to_internal_value(normalized)