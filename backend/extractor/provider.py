"""Stripe UPI provider flow helpers.

This module ports the provider-side helpers from ``upi_extract.py`` and
keeps all mutable state on ``ExtractionContext``.  The orchestration entry
points remain in ``extract.py`` so these functions can be tested in isolation.
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import requests

from .checkout import (
    STRIPE_RUNTIME_VERSION_DEFAULT,
    STRIPE_VERSION_FULL,
    amount_from_payload,
    build_chatgpt_session,
    is_checkout_not_active_error,
    processor_entity_for_country,
)
from .config import (
    CHATGPT_TIMEOUT,
    DEFAULT_TIMEOUT,
    IN_BILLING_ADDRESSES,
    IN_BILLING_NAMES,
)
from .proxy import normalize_country, proxy_label

if TYPE_CHECKING:
    from .context import ExtractionContext

EMAIL_DOMAINS = ("gmail.com", "outlook.com", "icloud.com", "hotmail.com")


def _cfg_bool(ctx: "ExtractionContext", primary: str, default: bool = False, alias: str = "") -> bool:
    value = ctx.cfg_bool(primary, default)
    return ctx.cfg_bool(alias, value) if alias else value


def _cfg_int(
    ctx: "ExtractionContext",
    primary: str,
    default: int,
    minimum: int = 1,
    alias: str = "",
) -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: "ExtractionContext", primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def random_runtime_version(ctx: "ExtractionContext" | None = None) -> str:
    if ctx is None:
        return STRIPE_RUNTIME_VERSION_DEFAULT
    return _cfg_str(ctx, "stripe_runtime_version", STRIPE_RUNTIME_VERSION_DEFAULT, "PP_RUNTIME_VERSION")


def stripe_browser_id() -> str:
    return f"{uuid.uuid4()}{uuid.uuid4().hex[:8]}"


def build_email(first_name: str, last_name: str) -> str:
    first = re.sub(r"[^a-z]", "", first_name.lower()) or "aisha"
    last = re.sub(r"[^a-z]", "", last_name.lower()) or "sharma"
    suffix = random.randint(10000, 999999)
    domain = random.choice(EMAIL_DOMAINS)
    local = f"{first}.{last}{suffix}" if random.random() < 0.5 else f"{first}{last}{suffix}"
    return f"{local}@{domain}"


def _payment_elements_locale(ctx: "ExtractionContext") -> str:
    return _cfg_str(ctx, "elements_locale", "en", "UPI_ELEMENTS_LOCALE") or "en"


def _saved_payment_value(ctx: "ExtractionContext") -> str:
    return _cfg_str(ctx, "saved_payment_value", "never", "UPI_SAVED_PAYMENT_VALUE") or "never"


def _default_upi_billing(ctx: "ExtractionContext") -> dict[str, str]:
    return {
        "email": _cfg_str(ctx, "billing_email", "redacted@example.invalid", "UPI_EMAIL"),
        "name": _cfg_str(ctx, "billing_name", "Aisha Sharma", "UPI_NAME"),
        "country": _cfg_str(ctx, "billing_country", "IN", "UPI_BILLING_COUNTRY"),
        "line1": _cfg_str(ctx, "billing_line1", "24 Park Street", "UPI_LINE1"),
        "line2": _cfg_str(ctx, "billing_line2", "", "UPI_LINE2"),
        "city": _cfg_str(ctx, "billing_city", "Kolkata", "UPI_CITY"),
        "postal_code": _cfg_str(ctx, "billing_postal_code", "700016", "UPI_POSTAL_CODE"),
        "state": _cfg_str(ctx, "billing_state", "WB", "UPI_STATE"),
    }


def _billing_config_overrides(ctx: "ExtractionContext") -> dict[str, str]:
    defaults = {
        "billing_email": ("email", "redacted@example.invalid", "UPI_EMAIL"),
        "billing_name": ("name", "Aisha Sharma", "UPI_NAME"),
        "billing_country": ("country", "IN", "UPI_BILLING_COUNTRY"),
        "billing_line1": ("line1", "24 Park Street", "UPI_LINE1"),
        "billing_line2": ("line2", "", "UPI_LINE2"),
        "billing_city": ("city", "Kolkata", "UPI_CITY"),
        "billing_postal_code": ("postal_code", "700016", "UPI_POSTAL_CODE"),
        "billing_state": ("state", "WB", "UPI_STATE"),
    }
    result: dict[str, str] = {}
    for config_key, (profile_key, default, alias) in defaults.items():
        value = _cfg_str(ctx, config_key, default, alias)
        if value != default:
            result[profile_key] = value
    return result


def build_ctx(
    ctx: "ExtractionContext",
    init_payload: dict[str, Any],
    checkout: dict[str, str],
) -> dict[str, Any]:
    client_context = init_payload.get("_client_context")
    if not isinstance(client_context, dict):
        client_context = {}
    return {
        "stripe_js_id": str(client_context.get("stripe_js_id") or init_payload.get("client_stripe_js_id") or uuid.uuid4()),
        "client_session_id": str(uuid.uuid4()),
        "guid": stripe_browser_id(),
        "muid": stripe_browser_id(),
        "sid": stripe_browser_id(),
        "elements_session_id": f"elements_session_{uuid.uuid4().hex[:11]}",
        "elements_session_config_id": str(init_payload.get("config_id") or uuid.uuid4()),
        "config_id": init_payload.get("config_id") or "",
        "init_checksum": init_payload.get("init_checksum") or "",
        "checkout_amount": amount_from_payload(init_payload),
        "locale": _payment_elements_locale(ctx),
        "currency": str(init_payload.get("currency") or checkout.get("currency") or "eur").lower(),
        "runtime_version": random_runtime_version(ctx),
        "stripe_version": STRIPE_VERSION_FULL,
    }


def stripe_elements_session_params(stripe_ctx: dict[str, Any]) -> dict[str, str]:
    return {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[session_id]": str(stripe_ctx.get("elements_session_id") or f"elements_session_{uuid.uuid4().hex[:11]}"),
        "elements_session_client[stripe_js_id]": str(stripe_ctx.get("stripe_js_id") or uuid.uuid4()),
        "elements_session_client[locale]": str(stripe_ctx.get("locale") or "en"),
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
    }


def upi_billing_profile(ctx: "ExtractionContext") -> dict[str, str]:
    name_entry = random.choice(IN_BILLING_NAMES)
    if isinstance(name_entry, str):
        parts = name_entry.split()
        first_name = parts[0] if parts else "Aisha"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else "Sharma"
    else:
        first_name, last_name = name_entry

    address_entry = random.choice(IN_BILLING_ADDRESSES)
    if isinstance(address_entry, dict):
        line1 = address_entry.get("line1", "24 Park Street")
        city = address_entry.get("city", "Kolkata")
        postal_code = address_entry.get("postal_code", "700016")
        state = address_entry.get("state", "WB")
    else:
        line1, city, postal_code, state = address_entry

    profile = {
        "email": build_email(first_name, last_name),
        "name": f"{first_name} {last_name}",
        "country": "IN",
        "line1": line1,
        "line2": "",
        "city": city,
        "postal_code": postal_code,
        "state": state,
    }
    if _cfg_bool(ctx, "use_fixed_billing", False, "UPI_USE_FIXED_BILLING"):
        profile = _default_upi_billing(ctx)
    else:
        profile.update(_billing_config_overrides(ctx))
    profile["country"] = normalize_country(profile.get("country", "IN"))
    return {key: str(value) for key, value in profile.items()}


def stripe_update_customer_data(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    cs_id: str,
    stripe_pk: str,
    stripe_ctx: dict[str, Any],
    billing: dict[str, str],
) -> bool:
    body: dict[str, Any] = {
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
        "expected_amount": str(stripe_ctx.get("checkout_amount") or 0),
        "elements_session_client[session_id]": stripe_ctx["elements_session_id"],
        "elements_session_client[stripe_js_id]": stripe_ctx["stripe_js_id"],
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[locale]": stripe_ctx["locale"],
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "customer_data[email]": billing["email"],
        "customer_data[name]": billing["name"],
        "customer_data[address][country]": billing["country"],
        "customer_data[address][line1]": billing["line1"],
        "customer_data[address][city]": billing["city"],
        "customer_data[address][postal_code]": billing["postal_code"],
    }
    if billing.get("line2"):
        body["customer_data[address][line2]"] = billing["line2"]
    if billing.get("state"):
        body["customer_data[address][state]"] = billing["state"]

    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}"
    try:
        resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
        ctx.dump_http(resp, "customer_data_update_br", body, "POST", url, force=resp.status_code >= 400)
        if resp.status_code < 400:
            ctx.log(f"IN customer_data submitted: {billing['name']} / {billing['city']} / {billing['postal_code']}")
            return True
        if is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        ctx.log(f"IN customer_data failed HTTP {resp.status_code}: {resp.text[:180]}", "[WARN] ")
    except Exception as exc:
        if is_checkout_not_active_error(exc):
            raise
        ctx.log(f"IN customer_data exception: {exc}", "[WARN] ")
    return False


def stripe_update_tax_region(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    cs_id: str,
    stripe_pk: str,
    stripe_ctx: dict[str, Any],
    billing: dict[str, str],
) -> bool:
    body: dict[str, Any] = {
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
        "elements_session_client[session_id]": stripe_ctx["elements_session_id"],
        "elements_session_client[stripe_js_id]": stripe_ctx["stripe_js_id"],
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_session_client[locale]": stripe_ctx["locale"],
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_options_client[saved_payment_method][enable_save]": _saved_payment_value(ctx),
        "elements_options_client[saved_payment_method][enable_redisplay]": _saved_payment_value(ctx),
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "expressCheckout",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][2]": "address",
        "tax_region[country]": billing["country"],
        "tax_region[postal_code]": billing["postal_code"],
        "tax_region[line1]": billing["line1"],
        "tax_region[city]": billing["city"],
    }
    if billing.get("state"):
        body["tax_region[state]"] = billing["state"]

    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}"
    try:
        resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
        ctx.dump_http(resp, "tax_region_update", body, "POST", url, force=resp.status_code >= 400)
        if resp.status_code < 400:
            ctx.log(f"tax_region submitted: {billing['country']} / {billing['city']} {billing['postal_code']}")
            return True
        if is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        ctx.log(f"tax_region failed HTTP {resp.status_code}: {resp.text[:180]}", "[WARN] ")
    except Exception as exc:
        if is_checkout_not_active_error(exc):
            raise
        ctx.log(f"tax_region exception: {exc}", "[WARN] ")
    return False


def checkout_snapshot(
    ctx: "ExtractionContext",
    chatgpt: requests.Session,
    checkout: dict[str, str],
    billing: dict[str, str],
) -> None:
    cs_id = checkout["cs_id"]
    processor = processor_entity_for_country(checkout.get("billing_country", "IN"), checkout.get("processor_entity") or "")
    page_url = f"https://chatgpt.com/checkout/{processor}/{cs_id}"
    body = {
        "snapshot": {
            "billing_address": {
                "name": billing["name"],
                "address": {
                    "line1": billing["line1"],
                    "city": billing["city"],
                    "country": billing["country"],
                    "postal_code": billing["postal_code"],
                    "state": billing.get("state", ""),
                },
            }
        }
    }
    url = "https://chatgpt.com/backend-api/payments/checkout/snapshot"
    try:
        resp = chatgpt.post(
            url,
            json=body,
            headers={
                "Referer": page_url,
                "x-openai-target-path": "/backend-api/payments/checkout/snapshot",
                "x-openai-target-route": "/backend-api/payments/checkout/snapshot",
            },
            timeout=CHATGPT_TIMEOUT,
        )
        ctx.dump_http(resp, "checkout_snapshot", body, "POST", url, force=_cfg_bool(ctx, "dump_warmup", False, "UPI_DUMP_WARMUP") or resp.status_code >= 400)
        if resp.status_code >= 400:
            if is_checkout_not_active_error(resp.text):
                raise RuntimeError("checkout_not_active_session")
            ctx.log(f"checkout snapshot failed HTTP {resp.status_code}: {resp.text[:180]}", "[WARN] ")
        else:
            ctx.log("checkout snapshot submitted")
    except Exception as exc:
        if is_checkout_not_active_error(exc):
            raise
        ctx.log(f"checkout snapshot exception: {exc}", "[WARN] ")


def stripe_create_upi_pm(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    cs_id: str,
    stripe_pk: str,
    billing: dict[str, str],
    stripe_ctx: dict[str, Any],
) -> str:
    body: dict[str, Any] = {
        "billing_details[name]": billing.get("name") or "Aisha Sharma",
        "billing_details[email]": billing.get("email") or "redacted@example.invalid",
        "billing_details[address][country]": billing.get("country") or "IN",
        "billing_details[address][line1]": billing.get("line1") or "24 Park Street",
        "billing_details[address][city]": billing.get("city") or "Kolkata",
        "billing_details[address][postal_code]": billing.get("postal_code") or "700016",
        "type": "upi",
        "payment_user_agent": f"stripe.js/{random_runtime_version(ctx)}; stripe-js-v3/{random_runtime_version(ctx)}; payment-element; deferred-intent",
        "referrer": "https://chatgpt.com",
        "time_on_page": str(random.randint(25000, 55000)),
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "client_attribution_metadata[client_session_id]": str(stripe_ctx.get("stripe_js_id") or ""),
        "client_attribution_metadata[checkout_config_id]": stripe_ctx.get("config_id") or "",
        "client_attribution_metadata[elements_session_id]": stripe_ctx.get("elements_session_id") or "",
        "client_attribution_metadata[elements_session_config_id]": stripe_ctx.get("elements_session_config_id") or "",
        "client_attribution_metadata[merchant_integration_source]": "elements",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "2021",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    if billing.get("state"):
        body["billing_details[address][state]"] = billing["state"]

    url = "https://api.stripe.com/v1/payment_methods"
    resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "upi_pm", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        raise RuntimeError(f"create UPI PM failed HTTP {resp.status_code}: {resp.text[:500]}")
    pm_id = str((resp.json() or {}).get("id") or "")
    if not pm_id.startswith("pm_"):
        raise RuntimeError(f"create UPI PM response invalid: {resp.text[:300]}")
    return pm_id


def add_inline_upi_payment_method_data(
    ctx: "ExtractionContext",
    body: dict[str, Any],
    cs_id: str,
    billing: dict[str, str],
    stripe_ctx: dict[str, Any],
) -> None:
    body.update(
        {
            "payment_method_data[type]": "upi",
            "payment_method_data[allow_redisplay]": "limited",
            "payment_method_data[billing_details][name]": billing["name"],
            "payment_method_data[billing_details][email]": billing["email"],
            "payment_method_data[billing_details][address][country]": billing["country"],
            "payment_method_data[billing_details][address][line1]": billing["line1"],
            "payment_method_data[billing_details][address][city]": billing["city"],
            "payment_method_data[billing_details][address][postal_code]": billing["postal_code"],
            "payment_method_data[payment_user_agent]": f"stripe.js/{random_runtime_version(ctx)}; stripe-js-v3/{random_runtime_version(ctx)}; payment-element; deferred-intent",
            "payment_method_data[referrer]": "https://chatgpt.com",
            "payment_method_data[time_on_page]": str(random.randint(18000, 55000)),
            "payment_method_data[client_attribution_metadata][checkout_session_id]": cs_id,
            "payment_method_data[client_attribution_metadata][client_session_id]": stripe_ctx["stripe_js_id"],
            "payment_method_data[client_attribution_metadata][checkout_config_id]": stripe_ctx.get("config_id") or "",
            "payment_method_data[client_attribution_metadata][elements_session_id]": stripe_ctx["elements_session_id"],
            "payment_method_data[client_attribution_metadata][elements_session_config_id]": stripe_ctx["elements_session_config_id"],
            "payment_method_data[client_attribution_metadata][merchant_integration_source]": "elements",
            "payment_method_data[client_attribution_metadata][merchant_integration_subtype]": "payment-element",
            "payment_method_data[client_attribution_metadata][merchant_integration_version]": "2021",
            "payment_method_data[client_attribution_metadata][payment_intent_creation_flow]": "deferred",
            "payment_method_data[client_attribution_metadata][payment_method_selection_flow]": "automatic",
            "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][0]": "expressCheckout",
            "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][1]": "payment",
            "payment_method_data[client_attribution_metadata][merchant_integration_additional_elements][2]": "address",
        }
    )
    if billing.get("state"):
        body["payment_method_data[billing_details][address][state]"] = billing["state"]


def stripe_checkout_long_url(cs_id: str, country: str, processor_entity: str) -> str:
    processor = processor_entity_for_country(country, processor_entity)
    success = f"https://chatgpt.com/checkout/verify?stripe_session_id={cs_id}&processor_entity={processor}&plan_type=plus"
    return (
        f"https://checkout.stripe.com/c/pay/{cs_id}"
        f"?returned_from_redirect=true&ui_mode=custom&return_url={quote(success, safe='')}"
    )


def to_openai_pay_url(stripe_hosted_url: str) -> str:
    url = str(stripe_hosted_url or "").strip()
    if not url:
        return ""
    if url.startswith("https://checkout.stripe.com"):
        return "https://pay.openai.com" + url[len("https://checkout.stripe.com") :]
    parsed = urlsplit(url)
    if parsed.netloc.lower() == "checkout.stripe.com":
        return urlunsplit((parsed.scheme or "https", "pay.openai.com", parsed.path, parsed.query, parsed.fragment))
    return url


def stripe_confirm_return_url(cs_id: str, checkout: dict[str, str], stripe_hosted_url: str) -> str:
    country = normalize_country(checkout.get("billing_country") or "IN")
    processor = processor_entity_for_country(country, checkout.get("processor_entity") or "")
    success = f"https://chatgpt.com/checkout/verify?stripe_session_id={cs_id}&processor_entity={processor}&plan_type=plus"
    hosted = to_openai_pay_url(stripe_hosted_url) or stripe_checkout_long_url(cs_id, country, processor)
    if "pay.openai.com/" in hosted or "checkout.stripe.com/" in hosted:
        parsed = urlsplit(hosted)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("success_return_url", success)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return hosted


def stripe_confirm_upi(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    cs_id: str,
    pm_id: str,
    stripe_pk: str,
    init_payload: dict[str, Any],
    stripe_ctx: dict[str, Any],
    checkout: dict[str, str],
    stripe_hosted_url: str,
    billing: dict[str, str],
) -> dict[str, Any]:
    runtime_version = str(stripe_ctx.get("runtime_version") or random_runtime_version(ctx))
    body = {
        "eid": "NA",
        "expected_amount": _cfg_str(ctx, "expected_amount", "", "PP_EXPECTED_AMOUNT") or str(stripe_ctx.get("checkout_amount") or amount_from_payload(init_payload)),
        "expected_payment_method_type": "upi",
        "return_url": stripe_confirm_return_url(cs_id, checkout, stripe_hosted_url),
        "_stripe_version": str(stripe_ctx.get("stripe_version") or STRIPE_VERSION_FULL),
        "guid": str(stripe_ctx.get("guid") or stripe_browser_id()),
        "muid": str(stripe_ctx.get("muid") or stripe_browser_id()),
        "sid": str(stripe_ctx.get("sid") or stripe_browser_id()),
        "key": stripe_pk,
        "version": runtime_version,
        "init_checksum": str(init_payload.get("init_checksum") or stripe_ctx.get("init_checksum") or ""),
        "client_attribution_metadata[client_session_id]": str(stripe_ctx.get("client_session_id") or stripe_ctx["stripe_js_id"]),
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "client_attribution_metadata[checkout_config_id]": stripe_ctx.get("config_id") or "",
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_subtype]": "payment-element",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_intent_creation_flow]": "deferred",
        "client_attribution_metadata[payment_method_selection_flow]": "automatic",
        "client_attribution_metadata[elements_session_id]": stripe_ctx["elements_session_id"],
        "client_attribution_metadata[elements_session_config_id]": stripe_ctx["elements_session_config_id"],
        "client_attribution_metadata[merchant_integration_additional_elements][0]": "payment",
        "client_attribution_metadata[merchant_integration_additional_elements][1]": "address",
        "consent[terms_of_service]": "accepted",
        "link_brand": "link",
    }
    body.update(stripe_elements_session_params(stripe_ctx))
    if _cfg_bool(ctx, "confirm_inline_pm", False, "UPI_CONFIRM_INLINE_PM"):
        add_inline_upi_payment_method_data(ctx, body, cs_id, billing, stripe_ctx)
    else:
        body["payment_method"] = pm_id
    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}/confirm"
    resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "upi_confirm", body, "POST", url, force=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"UPI confirm failed HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json() or {}


def collect_urls(payload: Any, urls: list[str] | None = None) -> list[str]:
    found = urls if urls is not None else []
    if isinstance(payload, str):
        for match in re.findall(r"https?://[^\s\"'<>]+", payload):
            found.append(match.rstrip("),.;]"))
        for match in re.findall(r"data:image/(?:png|svg\+xml|jpeg);base64,[A-Za-z0-9+/=]+", payload):
            found.append(match)
    elif isinstance(payload, dict):
        for value in payload.values():
            collect_urls(value, found)
    elif isinstance(payload, list):
        for item in payload:
            collect_urls(item, found)
    return found


def collect_strings(payload: Any, result: list[str] | None = None) -> list[str]:
    values = result if result is not None else []
    if isinstance(payload, str):
        values.append(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            collect_strings(value, values)
    elif isinstance(payload, list):
        for item in payload:
            collect_strings(item, values)
    return values


def is_resource_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    if is_known_static_host(url):
        return True
    return path.endswith((".js", ".css", ".map", ".woff", ".woff2", ".ttf", ".otf", ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"))


def is_known_static_host(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return host in {
        "stripe-camo.global.ssl.fastly.net",
        "files.stripe.com",
        "js.stripe.com",
        "m.stripe.network",
        "q.stripe.com",
    }


def is_upi_instructions_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.netloc or "").lower() == "payments.stripe.com"
        and (parsed.path or "").lower().startswith("/upi/instructions/")
    )


def is_redirect_like_url(url: str, from_action_field: bool = False) -> bool:
    if not isinstance(url, str):
        return False
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return False
    if is_resource_url(url):
        return False
    if is_upi_instructions_url(url):
        return True
    if from_action_field:
        return True

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    text = f"{host}{path}?{query}"
    if host in {"hooks.stripe.com", "payments.stripe.com"}:
        return True
    return any(part in text for part in ("upi", "/redirect/", "redirect_to_url", "authenticate"))


def is_qr_candidate(url: str) -> bool:
    lower = url.lower()
    return lower.startswith("data:image/") or "qr" in lower or "qrcode" in lower or "qr-code" in lower


def extract_qr_candidates(payload: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for url in collect_urls(payload):
        if url in seen:
            continue
        seen.add(url)
        if is_qr_candidate(url) and not is_known_static_host(url):
            result.append(url)
    return result


def find_submission_attempt(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        value = payload.get("submission_attempt")
        if isinstance(value, dict):
            return value
        for item in payload.values():
            nested = find_submission_attempt(item)
            if nested:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = find_submission_attempt(item)
            if nested:
                return nested
    return {}


def extract_redirect_url(payload: Any, path: tuple[str, ...] = ()) -> str:
    _ = path
    if isinstance(payload, dict):
        next_action = payload.get("next_action")
        if isinstance(next_action, dict):
            hosted_instructions_url = str(next_action.get("hosted_instructions_url") or "").strip()
            if is_upi_instructions_url(hosted_instructions_url):
                return hosted_instructions_url
            redirect = next_action.get("redirect_to_url")
            if isinstance(redirect, dict):
                url = str(redirect.get("url") or "").strip()
                if is_redirect_like_url(url, True):
                    return url
            for key in ("url", "redirect_url", "redirect_to_url", "hosted_url", "hosted_instructions_url"):
                value = next_action.get(key)
                if is_redirect_like_url(value, True):
                    return value

        for key in ("hosted_instructions_url", "redirect_url", "redirect_to_url", "authorization_url", "authentication_url"):
            value = payload.get(key)
            if is_redirect_like_url(value, True):
                return value

        for key, value in payload.items():
            nested = extract_redirect_url(value, path + (str(key),))
            if nested:
                return nested
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            nested = extract_redirect_url(item, path + (str(index),))
            if nested:
                return nested
    return ""


def first_value_by_key(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = first_value_by_key(value, key)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = first_value_by_key(item, key)
            if found not in (None, "", [], {}):
                return found
    return None


def setup_intent_last_error(payload: Any, current_pm_id: str = "") -> str:
    if isinstance(payload, dict):
        payload_id = str(payload.get("id") or "").strip()
        is_setup_intent = payload.get("object") == "setup_intent" or payload_id.startswith("seti_")
        last_error = payload.get("last_setup_error") if is_setup_intent else None
        setup_intent = payload.get("setup_intent")
        if not last_error and isinstance(setup_intent, dict):
            last_error = setup_intent.get("last_setup_error")
        if last_error:
            if current_pm_id and isinstance(last_error, dict):
                error_pm = last_error.get("payment_method")
                error_pm_id = ""
                if isinstance(error_pm, dict):
                    error_pm_id = str(error_pm.get("id") or "").strip()
                elif isinstance(error_pm, str):
                    error_pm_id = error_pm.strip()
                if error_pm_id and error_pm_id != current_pm_id:
                    last_error = None
            if last_error:
                try:
                    return json.dumps(last_error, ensure_ascii=False)[:700]
                except Exception:
                    return str(last_error)[:700]
        for value in payload.values():
            found = setup_intent_last_error(value, current_pm_id=current_pm_id)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = setup_intent_last_error(value, current_pm_id=current_pm_id)
            if found:
                return found
    return ""


def raise_if_setup_intent_blocked(payload: Any, context: str, current_pm_id: str = "") -> None:
    last_error = setup_intent_last_error(payload, current_pm_id=current_pm_id)
    if not last_error:
        return
    if "generic_decline" in last_error.lower():
        raise RuntimeError(f"Stripe risk rejected generic_decline: {context} SetupIntent did not produce redirect_url")
    raise RuntimeError(f"{context}: setup_intent.last_setup_error: {last_error}")


def should_retry_second_confirm_after_approve(error: Any) -> bool:
    text = str(error or "").lower()
    return (
        "checkout_upcoming_invoice_mismatch" in text
        or "redirect url resolution timeout" in text
        or "missing_redirect" in text
    )


def stripe_intent_redirect_url(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    intent_payload: Any,
    stripe_pk: str,
    current_pm_id: str = "",
) -> str:
    if not isinstance(intent_payload, dict):
        return ""
    intent_id = str(intent_payload.get("id") or "").strip()
    client_secret = str(intent_payload.get("client_secret") or "").strip()
    if not intent_id or not client_secret:
        return ""
    intent_object = str(intent_payload.get("object") or "").strip()
    intent_path = "setup_intents" if intent_object == "setup_intent" or intent_id.startswith("seti_") else "payment_intents"
    params = {"key": stripe_pk, "client_secret": client_secret}
    url = f"https://api.stripe.com/v1/{intent_path}/{intent_id}"
    resp = stripe.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "stripe_intent_get", params, "GET", url, force=resp.status_code >= 400)
    if resp.status_code != 200:
        return ""
    try:
        payload = resp.json() or {}
    except Exception:
        payload = {"_raw_text": resp.text}
    raise_if_setup_intent_blocked(payload, "stripe intent", current_pm_id=current_pm_id)
    redirect_url = extract_redirect_url(payload)
    if redirect_url:
        ctx.dump_http(resp, "stripe_intent_redirect", params, "GET", url, force=True)
        ctx.log(f"Stripe intent redirect_url found: {redirect_url[:180]}")
    return redirect_url


def stripe_payload_intent_redirect_url(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    payload: Any,
    stripe_pk: str,
    current_pm_id: str = "",
) -> str:
    if not isinstance(payload, dict):
        return ""
    for intent_key in ("setup_intent", "payment_intent"):
        candidates: list[Any] = []
        direct = payload.get(intent_key)
        if isinstance(direct, dict):
            candidates.append(direct)
        nested = first_value_by_key(payload, intent_key)
        if isinstance(nested, dict) and all(nested is not item for item in candidates):
            candidates.append(nested)
        for intent_payload in candidates:
            redirect_url = stripe_intent_redirect_url(ctx, stripe, intent_payload, stripe_pk, current_pm_id=current_pm_id)
            if redirect_url:
                return redirect_url
    return ""


def infer_processor_entity(payload: Any) -> str:
    for value in collect_strings(payload):
        match = re.search(r"[?&]processor_entity=([A-Za-z0-9_]+)", value)
        if match:
            return match.group(1)
    return ""


def payment_page_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    elements_options = payload.get("elements_options") if isinstance(payload.get("elements_options"), dict) else {}
    submission = find_submission_attempt(payload)
    next_action = first_value_by_key(payload, "next_action")
    payment_intent = first_value_by_key(payload, "payment_intent")
    setup_intent = first_value_by_key(payload, "setup_intent")
    summary: dict[str, Any] = {
        "object": payload.get("object"),
        "id": payload.get("id"),
        "status": payload.get("status"),
        "payment_status": payload.get("payment_status"),
        "amount": elements_options.get("amount") if elements_options else first_value_by_key(payload, "amount"),
        "currency": payload.get("currency") or (elements_options.get("currency") if elements_options else None),
        "mode": elements_options.get("mode") if elements_options else payload.get("mode"),
        "payment_method_types": elements_options.get("payment_method_types") if elements_options else None,
        "submission_state": submission.get("state") if submission else None,
        "submission_status": submission.get("status") if submission else None,
        "has_next_action": isinstance(next_action, dict) and bool(next_action),
    }
    if isinstance(payment_intent, dict):
        summary["payment_intent_status"] = payment_intent.get("status")
    elif isinstance(payment_intent, str):
        summary["payment_intent"] = payment_intent
    if isinstance(setup_intent, dict):
        summary["setup_intent_status"] = setup_intent.get("status")
    elif isinstance(setup_intent, str):
        summary["setup_intent"] = setup_intent
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def log_payment_page_summary(ctx: "ExtractionContext", stage: str, payload: Any) -> None:
    summary = payment_page_summary(payload)
    if not summary:
        return
    compact = format_payment_summary(summary)
    ctx.log(f"{stage} summary: {compact}")


def format_payment_summary(summary: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in summary.items())


def warmup_approve_context(
    ctx: "ExtractionContext",
    chatgpt: requests.Session,
    checkout_page_url: str,
) -> None:
    _ = checkout_page_url
    url = "https://chatgpt.com/backend-api/sentinel/ping"
    try:
        resp = chatgpt.post(
            url,
            json={},
            headers={
                "Referer": "https://chatgpt.com/",
                "x-openai-target-path": "/backend-api/sentinel/ping",
                "x-openai-target-route": "/backend-api/sentinel/ping",
            },
            timeout=CHATGPT_TIMEOUT,
        )
        ctx.dump_http(resp, "sentinel_ping", {}, "POST", url, force=_cfg_bool(ctx, "dump_warmup", False, "UPI_DUMP_WARMUP"))
    except Exception as exc:
        ctx.log(f"approve sentinel exception: {exc}", "[WARN] ")


def chatgpt_approve(ctx: "ExtractionContext", chatgpt: requests.Session, checkout: dict[str, str]) -> None:
    cs_id = checkout["cs_id"]
    processor = processor_entity_for_country(checkout.get("billing_country", "IN"), checkout.get("processor_entity", ""))
    page_url = f"https://chatgpt.com/checkout/{processor}/{cs_id}"
    if _cfg_bool(ctx, "approve_warmup", True, "UPI_APPROVE_WARMUP"):
        warmup_approve_context(ctx, chatgpt, page_url)
        time.sleep(random.uniform(0.8, 1.6))

    body = {"checkout_session_id": cs_id, "processor_entity": processor}
    headers = {
        "Referer": page_url,
        "x-openai-target-path": "/backend-api/payments/checkout/approve",
        "x-openai-target-route": "/backend-api/payments/checkout/approve",
    }
    url = "https://chatgpt.com/backend-api/payments/checkout/approve"
    resp = chatgpt.post(url, json=body, headers=headers, timeout=CHATGPT_TIMEOUT)
    ctx.dump_http(resp, "approve", body, "POST", url, force=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"ChatGPT approve failed HTTP {resp.status_code}: {resp.text[:300]}")
    result = ""
    try:
        result = str((resp.json() or {}).get("result") or "")
    except Exception:
        pass
    if result != "approved":
        raise RuntimeError(f"ChatGPT approve rejected: {result or resp.text[:200]}")


def approve_attempt(
    ctx: "ExtractionContext",
    access_token: str,
    device_id: str,
    checkout: dict[str, str],
    session_token: str,
    proxy: str,
    index: int,
    attempt_count: int,
) -> None:
    ctx.log(f"approve attempt {index}/{attempt_count} / proxy={proxy_label(proxy)}")
    chatgpt = build_chatgpt_session(ctx, access_token, device_id, proxy, session_token)
    chatgpt_approve(ctx, chatgpt, checkout)


def log_approve_failure(ctx: "ExtractionContext", error: str) -> bool:
    ctx.log(f"approve failed: {error[:180]}", "[WARN] ")
    if "ChatGPT approve rejected: blocked" in error or "ChatGPT approve failed: blocked" in error:
        ctx.log("approve returned blocked; treating as account/checkout risk, not proxy health", "[WARN] ")
        return True
    return False


def is_approve_failure_error(error: str) -> bool:
    text = str(error or "").lower()
    return "approve" in text or "chatgpt approve" in text


def approve_with_retry(
    ctx: "ExtractionContext",
    access_token: str,
    device_id: str,
    checkout: dict[str, str],
    proxies: list[str],
    session_token: str,
    proxy_group: str = "provider",
) -> str:
    _ = proxy_group
    max_retry = _cfg_int(ctx, "approve_retry_max", 10, alias="UPI_APPROVE_RETRY_MAX")
    parallel = _cfg_int(ctx, "approve_parallel", 1, alias="UPI_APPROVE_PARALLEL")
    last_error = ""
    if max_retry <= 0:
        raise RuntimeError("approve retry count must be positive")
    proxies = [proxy for proxy in dict.fromkeys(proxies) if proxy]
    if not proxies:
        raise RuntimeError("approve proxy list is empty")
    sticky = _cfg_bool(ctx, "approve_sticky", True, "UPI_APPROVE_STICKY")
    if sticky and parallel > 1:
        ctx.log("approve sticky mode uses serial proxy failover")
        parallel = 1
    if sticky:
        selected_proxies = proxies[:max_retry]
    else:
        attempt_count = min(max_retry, len(proxies))
        fixed_proxies = proxies[: min(2, attempt_count)]
        selected_proxies = fixed_proxies[:]
        remain_count = attempt_count - len(selected_proxies)
        if remain_count > 0:
            selected_proxies.extend(random.sample(proxies[2:], min(remain_count, len(proxies) - 2)))
    attempt_count = len(selected_proxies)
    ctx.log(f"approve proxy strategy: {'sticky' if sticky else 'rotate'}")
    if parallel > 1:
        workers = min(parallel, attempt_count)
        ctx.log(f"approve parallel: workers={workers}, attempts={attempt_count}")
        blocked_count = 0
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    approve_attempt,
                    ctx,
                    access_token,
                    device_id,
                    checkout,
                    session_token,
                    proxy,
                    index,
                    attempt_count,
                ): proxy
                for index, proxy in enumerate(selected_proxies, start=1)
            }
            for future in as_completed(futures):
                try:
                    future.result()
                    ctx.log("approve succeeded")
                    for pending in futures:
                        pending.cancel()
                    return futures[future]
                except Exception as exc:
                    last_error = str(exc)
                    if is_checkout_not_active_error(last_error):
                        raise RuntimeError("checkout_not_active_session")
                    if log_approve_failure(ctx, last_error):
                        blocked_count += 1
        if blocked_count and blocked_count == attempt_count:
            raise RuntimeError("approve blocked")
        raise RuntimeError(f"approve retry failed: {last_error}")

    blocked_count = 0
    for index, proxy in enumerate(selected_proxies, start=1):
        try:
            approve_attempt(ctx, access_token, device_id, checkout, session_token, proxy, index, attempt_count)
            ctx.log("approve succeeded")
            return proxy
        except Exception as exc:
            last_error = str(exc)
            if is_checkout_not_active_error(last_error):
                raise RuntimeError("checkout_not_active_session")
            if log_approve_failure(ctx, last_error):
                blocked_count += 1
            if index < attempt_count:
                time.sleep(random.uniform(1, 2))
    if blocked_count and blocked_count == attempt_count:
        raise RuntimeError("approve blocked")
    raise RuntimeError(f"approve retry failed: {last_error}")


def poll_payment_page(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    checkout: dict[str, str],
    stripe_pk: str,
    stripe_ctx: dict[str, Any],
    current_pm_id: str = "",
) -> tuple[str, list[str]]:
    cs_id = checkout["cs_id"]
    deadline = time.time() + _cfg_int(ctx, "poll_timeout", 45, alias="UPI_POLL_TIMEOUT")
    params = {
        **stripe_elements_session_params(stripe_ctx),
        "key": stripe_pk,
        "_stripe_version": str(stripe_ctx.get("stripe_version") or STRIPE_VERSION_FULL),
    }
    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}"
    last_error = ""
    last_payload: dict[str, Any] = {}
    last_summary = ""
    while time.time() < deadline:
        resp = stripe.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        if resp.status_code >= 400:
            ctx.dump_http(resp, "poll_error", params, "GET", url, force=True)
            if is_checkout_not_active_error(resp.text):
                raise RuntimeError("checkout_not_active_session")
            last_error = f"HTTP {resp.status_code}"
            time.sleep(1)
            continue
        try:
            payload = resp.json() or {}
        except Exception:
            payload = {"_raw_text": resp.text}
        raise_if_setup_intent_blocked(payload, "stripe payment_pages", current_pm_id=current_pm_id)
        last_payload = payload
        summary = payment_page_summary(payload)
        summary_text = format_payment_summary(summary) if summary else ""
        if summary_text and summary_text != last_summary:
            last_summary = summary_text
            ctx.log(f"poll summary: {summary_text}")
        redirect_url = extract_redirect_url(payload)
        qr_urls = extract_qr_candidates(payload)
        if redirect_url or qr_urls:
            ctx.dump_http(resp, "poll_success", params, "GET", url, force=True)
            return redirect_url, qr_urls
        intent_redirect = stripe_payload_intent_redirect_url(ctx, stripe, payload, stripe_pk, current_pm_id=current_pm_id)
        if intent_redirect:
            ctx.dump_http(resp, "poll_success", params, "GET", url, force=True)
            return intent_redirect, qr_urls
        submission = find_submission_attempt(payload)
        if submission.get("state") == "requires_approval":
            last_error = "payment_pages still requires_approval"
            time.sleep(1)
            continue
        if submission.get("state") == "failed":
            ctx.dump_http(resp, "poll_failed", params, "GET", url, force=True)
            raise RuntimeError(f"Stripe submission failed: {submission}")
        last_error = str(submission or "waiting")
        time.sleep(1)
    if last_payload:
        dump_response = type("DumpResponse", (), {})()
        dump_response.status_code = 200
        dump_response.url = url
        dump_response.text = json.dumps(last_payload, ensure_ascii=False, indent=2)
        ctx.dump_http(dump_response, "poll_no_redirect", params, "GET", url, force=True)
    ctx.log(f"poll ended without UPI redirect/QR: {last_error}", "[WARN] ")
    raise RuntimeError(f"redirect url resolution timeout: {last_error}")


def fetch_redirect_page(ctx: "ExtractionContext", stripe: requests.Session, start_url: str) -> list[str]:
    if not start_url or not _cfg_bool(ctx, "follow_redirect", True, "UPI_FOLLOW_REDIRECT"):
        return []
    current = start_url
    qr_urls: list[str] = []
    for hop in range(1, 6):
        resp = stripe.get(current, timeout=DEFAULT_TIMEOUT, allow_redirects=False)
        ctx.dump_http(resp, f"redirect_hop_{hop}", None, "GET", current, force=True)
        qr_urls.extend(extract_qr_candidates(resp.text))
        location = resp.headers.get("location") or resp.headers.get("Location") or ""
        if not location:
            break
        current = urljoin(current, location)
    return list(dict.fromkeys(qr_urls))


def resolve_external_redirect(ctx: "ExtractionContext", stripe: requests.Session, start_url: str) -> str:
    if not start_url or not _cfg_bool(ctx, "follow_redirect", True, "UPI_FOLLOW_REDIRECT"):
        return start_url
    current = start_url
    for hop in range(1, 6):
        if is_upi_instructions_url(current):
            return current
        try:
            resp = stripe.get(current, timeout=DEFAULT_TIMEOUT, allow_redirects=False)
            ctx.dump_http(resp, f"resolve_redirect_hop_{hop}", None, "GET", current, force=True)
        except Exception as exc:
            ctx.log(f"follow redirect exception: {exc}", "[WARN] ")
            return current
        location = resp.headers.get("location") or resp.headers.get("Location") or ""
        if not location:
            return current
        current = urljoin(current, location)
    return current


def approve_proxy_candidates(
    ctx: "ExtractionContext",
    checkout_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
) -> list[str]:
    approve_preferences = ctx.successful_approve_preferences(checkout_proxy, provider_proxy, [provider_proxy] + approve_pool)
    if approve_preferences:
        ctx.log(f"using historical approve proxy first: {proxy_label(approve_preferences[0])}")
    return list(dict.fromkeys(approve_preferences + [provider_proxy] + approve_pool))


def resolve_confirm_payload_upi(
    ctx: "ExtractionContext",
    stripe: requests.Session,
    confirm_payload: dict[str, Any],
    checkout: dict[str, str],
    stripe_pk: str,
    stripe_ctx: dict[str, Any],
    pm_id: str,
    access_token: str,
    device_id: str,
    session_token: str,
    checkout_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
) -> tuple[str, list[str], str]:
    raise_if_setup_intent_blocked(confirm_payload, "stripe confirm", current_pm_id=pm_id)
    redirect_url = extract_redirect_url(confirm_payload)
    if not redirect_url:
        redirect_url = stripe_payload_intent_redirect_url(ctx, stripe, confirm_payload, stripe_pk, current_pm_id=pm_id)
    qr_urls = extract_qr_candidates(confirm_payload)
    submission = find_submission_attempt(confirm_payload)

    if redirect_url:
        ctx.log(f"confirm found payment URL: {redirect_url[:180]}")
    if qr_urls:
        ctx.log(f"confirm found {len(qr_urls)} QR candidates")

    approve_proxy = ""
    if not redirect_url and submission.get("state") == "requires_approval":
        ctx.log("ChatGPT approve required")
        approve_proxies = approve_proxy_candidates(ctx, checkout_proxy, provider_proxy, approve_pool)
        ctx.log("approve required for UPI zero-amount flow; trying historical/current provider proxies first")
        approve_proxy = approve_with_retry(ctx, access_token, device_id, checkout, approve_proxies, session_token, "provider")
        ctx.log("polling final redirect after approve")
        redirect_url, poll_qr = poll_payment_page(ctx, stripe, checkout, stripe_pk, stripe_ctx, current_pm_id=pm_id)
        qr_urls.extend(poll_qr)
    elif not redirect_url and not qr_urls:
        ctx.log("confirm did not return UPI redirect/QR; polling payment_pages", "[WARN] ")
        redirect_url, poll_qr = poll_payment_page(ctx, stripe, checkout, stripe_pk, stripe_ctx, current_pm_id=pm_id)
        qr_urls.extend(poll_qr)

    return redirect_url, list(dict.fromkeys(qr_urls)), approve_proxy
