"""Constants, regexes, billing profiles — no shared state."""

import os
import re

# ── Stripe version / identifiers ──────────────────────────────────────────
STRIPE_VERSION_FULL = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
DEFAULT_STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
DEFAULT_STRIPE_RUNTIME_VERSION = "6f8494a281"

# ── Country configuration ─────────────────────────────────────────────────
UPI_BOOTSTRAP_COUNTRY = "IN"
UPI_PROVIDER_COUNTRY = "IN"
UPI_PROVIDER_COUNTRY_LABEL = "IN"
UPI_PROMOTION_COUNTRIES: list[str] = ["VN"]

COUNTRY_CURRENCY: dict[str, str] = {
    "AU": "AUD", "BR": "BRL", "CA": "CAD", "CH": "CHF", "CL": "CLP",
    "CO": "COP", "CZ": "CZK", "DK": "DKK", "EG": "EGP", "ES": "EUR",
    "EU": "EUR", "FR": "EUR", "DE": "EUR", "BE": "EUR", "NL": "EUR",
    "GB": "GBP", "HK": "HKD", "ID": "IDR", "IL": "ILS", "IN": "INR",
    "JP": "JPY", "KR": "KRW", "MX": "MXN", "MY": "MYR", "NG": "NGN",
    "NO": "NOK", "NZ": "NZD", "PE": "PEN", "PH": "PHP", "PK": "PKR",
    "PL": "PLN", "RO": "RON", "SA": "SAR", "SE": "SEK", "SG": "SGD",
    "TH": "THB", "TR": "TRY", "TW": "TWD", "UA": "UAH", "US": "USD",
    "VN": "VND", "ZA": "ZAR",
}

# ── Billing profiles (randomly selected) ──────────────────────────────────
IN_BILLING_NAMES: list[str] = [
    "Aisha Sharma", "Priya Patel", "Neha Gupta", "Ananya Reddy",
    "Kavita Iyer", "Divya Menon", "Riya Nair", "Sneha Joshi",
    "Meera Pillai", "Lakshmi Krishnan",
]

IN_BILLING_ADDRESSES: list[dict[str, str]] = [
    {"line1": "24 Park Street", "city": "Kolkata", "postal_code": "700016", "state": "West Bengal"},
    {"line1": "12 MG Road", "city": "Bengaluru", "postal_code": "560001", "state": "Karnataka"},
    {"line1": "45 Linking Road", "city": "Mumbai", "postal_code": "400050", "state": "Maharashtra"},
    {"line1": "78 Connaught Place", "city": "New Delhi", "postal_code": "110001", "state": "Delhi"},
    {"line1": "90 Anna Salai", "city": "Chennai", "postal_code": "600002", "state": "Tamil Nadu"},
    {"line1": "33 Banjara Hills", "city": "Hyderabad", "postal_code": "500034", "state": "Telangana"},
    {"line1": "56 Ashram Road", "city": "Ahmedabad", "postal_code": "380009", "state": "Gujarat"},
    {"line1": "15 Residency Road", "city": "Pune", "postal_code": "411001", "state": "Maharashtra"},
    {"line1": "22 Civil Lines", "city": "Jaipur", "postal_code": "302001", "state": "Rajasthan"},
    {"line1": "64 Hazratganj", "city": "Lucknow", "postal_code": "226001", "state": "Uttar Pradesh"},
]


# ── Timeouts ──────────────────────────────────────────────────────────────
CHATGPT_TIMEOUT = 45
DEFAULT_TIMEOUT = 30

# ── Error constants ───────────────────────────────────────────────────────
UPI_UNAVAILABLE_ERROR = "UPI_unavailable"

# ── Regexes ───────────────────────────────────────────────────────────────
_PROXY_COUNTRY_SELECTOR_RE = re.compile(
    r"(?i)(?P<name>country|region)(?P<separator>[-_=])(?P<value>[a-z]{2}(?:,[a-z]{2})*)"
)


def _make_country_selector_re() -> re.Pattern:
    return _PROXY_COUNTRY_SELECTOR_RE


def country_selector_re() -> re.Pattern:
    return _PROXY_COUNTRY_SELECTOR_RE
