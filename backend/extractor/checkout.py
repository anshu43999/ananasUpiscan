"""ChatGPT checkout flow — Stripe checkout session management.

Port of the checkout-related functions from upi_extract.py, adapted to use
ExtractionContext for configuration, logging, and HTTP dump recording.
All mutation of ExtractionContext (log, dump_http, config reads) stays
on the context instance; this module does not access module-level globals.
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

import requests

from .config import (
    CHATGPT_TIMEOUT,
    DEFAULT_STRIPE_PK,
    DEFAULT_TIMEOUT,
    UPI_BOOTSTRAP_COUNTRY,
    UPI_PROVIDER_COUNTRY,
)
from .proxy import (
    currency_for_country,
    normalize_country,
    proxy_label,
    set_proxy,
)
from .session import (
    CHATGPT_CLIENT_BUILD_NUMBER,
    CHATGPT_CLIENT_VERSION,
    DEFAULT_USER_AGENT,
    new_session,
)

if TYPE_CHECKING:
    from .context import ExtractionContext

# ── Stripe version string (original upi_extract.py format) ─────────────────
STRIPE_VERSION_FULL = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
STRIPE_RUNTIME_VERSION_DEFAULT = "6f8494a281"


# ═══════════════════════════════════════════════════════════════════════════
# Pure helpers — no ctx dependency
# ═══════════════════════════════════════════════════════════════════════════

def is_checkout_not_active_error(value: Any) -> bool:
    return "checkout_not_active_session" in str(value)


def is_user_already_paid_error(value: Any) -> bool:
    return "user is already paid" in str(value or "").lower()


def random_user_agent() -> str:
    return DEFAULT_USER_AGENT


# ── Locale helpers ─────────────────────────────────────────────────────────

def _cfg_bool(ctx: "ExtractionContext", primary: str, default: bool = False, alias: str = "") -> bool:
    value = ctx.cfg_bool(primary, default)
    return ctx.cfg_bool(alias, value) if alias else value


def _cfg_int(ctx: "ExtractionContext", primary: str, default: int, minimum: int = 1, alias: str = "") -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: "ExtractionContext", primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def _payment_browser_locale(ctx: "ExtractionContext") -> str:
    return _cfg_str(ctx, "browser_locale", "en-IN", "UPI_BROWSER_LOCALE") or "en-IN"


def _payment_elements_locale(ctx: "ExtractionContext") -> str:
    return _cfg_str(ctx, "elements_locale", "en", "UPI_ELEMENTS_LOCALE") or "en"


def _payment_browser_timezone(ctx: "ExtractionContext") -> str:
    return _cfg_str(ctx, "browser_timezone", "Asia/Kolkata", "UPI_BROWSER_TIMEZONE") or "Asia/Kolkata"


def _payment_accept_language(ctx: "ExtractionContext") -> str:
    locale = _payment_browser_locale(ctx)
    if locale.lower().startswith("en"):
        return "en-US,en;q=0.9"
    return f"{locale},{locale.split('-', 1)[0]};q=0.9,en;q=0.8"


# ── Processor entity ───────────────────────────────────────────────────────

def processor_entity_for_country(
    country: str,
    processor_entity: str = "",
) -> str:
    if processor_entity:
        return processor_entity
    return "openai_llc" if normalize_country(country) == "US" else "openai_ie"


# ── Response inspectors ────────────────────────────────────────────────────

def checkout_response_has_promo(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for key in (
        "scheduled_discount_preview",
        "immediate_discount_settings",
        "promo_campaign",
        "promo_credit_grant",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return True
    return False


def checkout_response_has_trial(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("one_click_trial_eligible") is True:
        return True
    subscription_data = payload.get("subscription_data")
    if isinstance(subscription_data, dict) and int(subscription_data.get("trial_period_days") or 0) > 0:
        return True
    for key in ("trial_period_days", "trial_end"):
        value = payload.get(key)
        if value not in (None, "", 0, "0", False):
            return True
    return False


def amount_from_payload(payload: Any) -> int:
    if isinstance(payload, dict):
        total_summary = payload.get("total_summary")
        if isinstance(total_summary, dict) and total_summary.get("due") is not None:
            return int(total_summary.get("due") or 0)
        invoice = payload.get("invoice")
        if isinstance(invoice, dict) and invoice.get("amount_due") is not None:
            return int(invoice.get("amount_due") or 0)
        line_items = payload.get("line_items")
        if isinstance(line_items, list):
            total = 0
            found = False
            for item in line_items:
                if isinstance(item, dict) and item.get("amount") is not None:
                    total += int(item.get("amount") or 0)
                    found = True
            if found:
                return total
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    for pattern in (
        r'"total"\s*:\s*(\d+)',
        r'"amount_total"\s*:\s*(\d+)',
        r'"checkout_amount"\s*:\s*(\d+)',
        r'"amount"\s*:\s*(\d+)',
    ):
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Context-dependent functions — require ExtractionContext
# ═══════════════════════════════════════════════════════════════════════════

def build_chatgpt_session(
    ctx: "ExtractionContext",
    access_token: str,
    device_id: str,
    proxy: str = "",
    session_token: str = "",
) -> requests.Session:
    session = new_session(ctx, proxy)
    cookie = f"oai-did={device_id}"
    if session_token:
        cookie += f"; __Secure-next-auth.session-token={session_token}"
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": _payment_accept_language(ctx),
            "Authorization": f"Bearer {access_token}",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "Content-Type": "application/json",
            "oai-device-id": device_id,
            "oai-language": _payment_browser_locale(ctx),
            "oai-session-id": device_id,
            "oai-client-version": CHATGPT_CLIENT_VERSION,
            "oai-client-build-number": CHATGPT_CLIENT_BUILD_NUMBER,
            "sec-ch-ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "Cookie": cookie,
        }
    )
    return session


def checkout_page_url(checkout: dict[str, str]) -> str:
    processor = processor_entity_for_country(
        UPI_BOOTSTRAP_COUNTRY,
        checkout.get("processor_entity") or "",
    )
    return f"https://chatgpt.com/checkout/{processor}/{checkout['cs_id']}"


def create_checkout(
    ctx: "ExtractionContext",
    chatgpt: requests.Session,
    country: str,
) -> dict[str, str]:
    country = normalize_country(country)
    promo_mode = (_cfg_str(ctx, "promo_mode", "campaign", "PP_PROMO_MODE") or "campaign").strip().lower()
    promo_id = (_cfg_str(ctx, "promo_id", "plus-1-month-free", "PP_PROMO_ID") or "plus-1-month-free").strip()
    body: dict[str, Any] = {
        "entry_point": _cfg_str(ctx, "entry_point", "all_plans_pricing_modal", "PP_ENTRY_POINT") or "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency_for_country(country)},
        "checkout_ui_mode": "custom",
    }
    if promo_mode in ("trial", "free_trial"):
        trial_days = _cfg_int(ctx, "trial_days", 30, alias="PP_TRIAL_DAYS")
        body["subscription_data"] = {"trial_period_days": trial_days}
    elif promo_mode in ("campaign", "query"):
        body["promo_campaign"] = {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": promo_mode == "query",
        }
    elif promo_mode == "coupon":
        body["coupon"] = promo_id
    elif promo_mode == "code":
        body["promotion_code"] = promo_id
    elif promo_mode != "off":
        ctx.log(f"未知 PP_PROMO_MODE={promo_mode!r}，已忽略", "[WARN] ")
    ctx.log(f"Checkout promo: mode={promo_mode}, id={promo_id}")

    headers = {
        "Referer": "https://chatgpt.com/",
        "x-openai-target-path": "/backend-api/payments/checkout",
        "x-openai-target-route": "/backend-api/payments/checkout",
    }
    url = "https://chatgpt.com/backend-api/payments/checkout"
    resp = chatgpt.post(url, json=body, headers=headers, timeout=CHATGPT_TIMEOUT)
    ctx.dump_http(resp, "checkout", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        if is_user_already_paid_error(resp.text):
            raise RuntimeError("用户已支付: User is already paid")
        raise RuntimeError(f"checkout 创建失败 HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json() or {}
    if (
        promo_mode == "coupon"
        and promo_id == "plus-1-month-free"
        and not checkout_response_has_promo(data)
        and _cfg_bool(ctx, "coupon_fallback_promo_campaign", True, "UPI_COUPON_FALLBACK_PROMO_CAMPAIGN")
    ):
        ctx.log("coupon 响应未显示优惠，按 promo_campaign 字符串重试", "[PROMO] ")
        fallback_body = dict(body)
        fallback_body.pop("coupon", None)
        fallback_body["promo_campaign"] = promo_id
        resp = chatgpt.post(url, json=fallback_body, headers=headers, timeout=CHATGPT_TIMEOUT)
        ctx.dump_http(resp, "checkout_promo_campaign", fallback_body, "POST", url, force=True)
        if resp.status_code >= 400:
            if is_user_already_paid_error(resp.text):
                raise RuntimeError("用户已支付: User is already paid")
            raise RuntimeError(f"checkout promo_campaign 重试失败 HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json() or {}
        ctx.log(f"promo_campaign 重试后 promo={checkout_response_has_promo(data)}", "[PROMO] ")

    cs_id = data.get("checkout_session_id") or data.get("session_id") or data.get("id")
    if not cs_id or not str(cs_id).startswith("cs_"):
        raise RuntimeError(f"checkout 响应缺少 cs_id: {str(data)[:500]}")

    raw_pk = (
        data.get("stripe_publishable_key")
        or data.get("publishable_key")
        or data.get("publishableKey")
        or data.get("stripePublishableKey")
        or data.get("key")
        or ""
    )
    match = re.search(r"pk_live_[A-Za-z0-9]+", str(raw_pk))
    stripe_pk = match.group(0) if match else _cfg_str(ctx, "stripe_pk", DEFAULT_STRIPE_PK, "STRIPE_PUBLISHABLE_KEY")
    processor_entity = str(data.get("processor_entity") or data.get("processorEntity") or "")
    ctx.log(
        f"Checkout 创建成功: {cs_id} / {country} / {currency_for_country(country)} / "
        f"mode={promo_mode} / promo={checkout_response_has_promo(data)} / "
        f"trial={checkout_response_has_trial(data)}"
    )
    return {
        "cs_id": str(cs_id),
        "processor_entity": processor_entity,
        "stripe_pk": stripe_pk,
        "billing_country": country,
        "currency": currency_for_country(country),
    }


def update_checkout_promotion(
    ctx: "ExtractionContext",
    chatgpt: requests.Session,
    checkout: dict[str, str],
    promotion_country: str,
) -> None:
    mode = (_cfg_str(ctx, "promo_mode", "campaign", "PP_PROMO_MODE") or "campaign").strip().lower()
    promo_id = (_cfg_str(ctx, "promo_id", "plus-1-month-free", "PP_PROMO_ID") or "plus-1-month-free").strip()
    body: dict[str, Any] = {
        "checkout_session_id": checkout["cs_id"],
        "processor_entity": processor_entity_for_country(
            ctx.bootstrap_country,
            checkout.get("processor_entity") or "",
        ),
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
    }
    if mode in {"campaign", "query", "coupon"}:
        body["promo_campaign"] = {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": mode == "query",
        }
    url = "https://chatgpt.com/backend-api/payments/checkout/update"
    resp = chatgpt.post(
        url,
        json=body,
        headers={
            "Referer": f"https://chatgpt.com/checkout/{processor_entity_for_country(ctx.bootstrap_country, checkout.get('processor_entity') or '')}/{checkout['cs_id']}",
            "x-openai-target-path": "/backend-api/payments/checkout/update",
            "x-openai-target-route": "/backend-api/payments/checkout/update",
        },
        timeout=CHATGPT_TIMEOUT,
    )
    ctx.dump_http(resp, "checkout_promotion_update", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        if is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        raise RuntimeError(f"checkout/update 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        payload = resp.json() or {}
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"checkout/update rejected: {str(payload)[:500]}")
    ctx.log(f"{promotion_country} checkout/update 成功: promo={promo_id if 'promo_campaign' in body else 'off'}")


def update_upi_checkout_taxes(
    ctx: "ExtractionContext",
    chatgpt: requests.Session,
    checkout: dict[str, str],
    billing: dict[str, str],
) -> None:
    url = "https://chatgpt.com/backend-api/payments/checkout/taxes"
    body: dict[str, Any] = {
        "checkout_session_id": checkout["cs_id"],
        "checkout_email": billing["email"],
        "billing_country": ctx.provider_country,
        "billing_name": billing["name"],
        "currency": currency_for_country(ctx.provider_country),
        "tax_id": None,
        "processor_entity": processor_entity_for_country(
            ctx.bootstrap_country,
            checkout.get("processor_entity") or "",
        ),
        "billing_address": {
            "line1": billing["line1"],
            "city": billing["city"],
            "country": ctx.provider_country,
            "postal_code": billing["postal_code"],
        },
    }
    if billing.get("state"):
        body["billing_address"]["state"] = billing["state"]
    resp = chatgpt.post(
        url,
        json=body,
        headers={
            "Referer": f"https://chatgpt.com/checkout/{processor_entity_for_country(ctx.bootstrap_country, checkout.get('processor_entity') or '')}/{checkout['cs_id']}",
            "x-openai-target-path": "/backend-api/payments/checkout/taxes",
            "x-openai-target-route": "/backend-api/payments/checkout/taxes",
        },
        timeout=CHATGPT_TIMEOUT,
    )
    ctx.dump_http(resp, "checkout_taxes", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        if is_checkout_not_active_error(resp.text):
            raise RuntimeError("checkout_not_active_session")
        raise RuntimeError(f"checkout/taxes 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    ctx.log(f"{ctx.provider_country} checkout/taxes 同步成功")


def stripe_init(
    ctx: "ExtractionContext",
    cs_id: str,
    stripe_pk: str,
    proxy: str = "",
) -> dict[str, Any]:
    stripe = new_session(ctx, proxy)
    stripe.headers.update(
        {
            "User-Agent": random_user_agent(),
            "Accept-Language": _payment_accept_language(ctx),
        }
    )
    stripe_js_id = str(uuid.uuid4())
    body = {
        "browser_locale": _payment_browser_locale(ctx),
        "browser_timezone": _payment_browser_timezone(ctx),
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": _payment_elements_locale(ctx),
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": stripe_pk,
        "_stripe_version": STRIPE_VERSION_FULL,
    }
    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}/init"
    resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "stripe_init", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        raise RuntimeError(f"Stripe init 失败 HTTP {resp.status_code}: {resp.text[:500]}")
    payload: dict[str, Any] = resp.json() or {}
    payload["client_stripe_js_id"] = stripe_js_id
    payload["_client_context"] = {"stripe_js_id": stripe_js_id}
    return payload
