"""Kakao Pay / Nicepay redirect extraction orchestration."""

from __future__ import annotations

import hashlib
import os
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import requests

from .checkout import (
    is_checkout_not_active_error,
    is_user_already_paid_error,
)
from .config import DEFAULT_TIMEOUT
from .context import ExtractionContext
from .provider import first_value_by_key, log_payment_page_summary
from .proxy import (
    currency_for_country,
    normalize_country,
    parse_proxy_chain_seed,
    proxy_chain_key,
    proxy_for_country,
    proxy_label,
)
from .session import new_session


STRIPE_VERSION = "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1"
STRIPE_RUNTIME = "c00af4ce81"
STRIPE_PAYMENT_UA = f"stripe.js/{STRIPE_RUNTIME}; stripe-js-v3/{STRIPE_RUNTIME}; checkout"
KAKAO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
KOREAN_FAMILY_NAMES = (
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황",
)
KOREAN_GIVEN_NAMES = (
    "민준", "서준", "도윤", "예준", "시우", "주원", "하준", "지호", "지후", "준서", "서연", "서윤",
    "지우", "서현", "하은", "하윤", "민서", "지유", "윤서", "채원",
)
SEOUL_ADDRESS_SEEDS = (
    {"district": "강남구", "road": "테헤란로", "postal": "06164", "base": 87, "span": 40},
    {"district": "강남구", "road": "봉은사로", "postal": "06097", "base": 524, "span": 32},
    {"district": "서초구", "road": "서초대로", "postal": "06611", "base": 396, "span": 36},
    {"district": "송파구", "road": "올림픽로", "postal": "05510", "base": 300, "span": 36},
    {"district": "마포구", "road": "월드컵북로", "postal": "03925", "base": 396, "span": 36},
)
EMAIL_DOMAINS = ("gmail.com", "naver.com", "daum.net", "kakao.com")


class TaskStopped(RuntimeError):
    pass


def _cfg_int(ctx: ExtractionContext, primary: str, default: int, minimum: int = 1, alias: str = "") -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: ExtractionContext, primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def ensure_running(stop_event: Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise TaskStopped("task stopped")


def response_error(response: Any, limit: int = 800) -> str:
    try:
        return str(response.text or "")[:limit]
    except Exception:
        return ""


def kakao_checkout_country(ctx: ExtractionContext) -> str:
    return normalize_country(_cfg_str(ctx, "checkout_country", ctx.bootstrap_country, "KAKAO_BOOTSTRAP_COUNTRY"))


def kakao_promotion_country(ctx: ExtractionContext) -> str:
    return normalize_country(_cfg_str(ctx, "kakao_promotion_country", ctx.promotion_country, "KAKAO_PROMOTION_COUNTRY"))


def kakao_provider_country(ctx: ExtractionContext) -> str:
    return normalize_country(_cfg_str(ctx, "provider_country", ctx.provider_country, "KAKAO_PROVIDER_COUNTRY"))


def random_kakao_billing(ctx: ExtractionContext, access_token: str) -> dict[str, str]:
    provider_country = kakao_provider_country(ctx)
    seed = hashlib.sha256(f"{access_token}:{uuid.uuid4()}".encode()).digest()
    rng = random.Random(seed)
    address = rng.choice(SEOUL_ADDRESS_SEEDS)
    name = f"{rng.choice(KOREAN_FAMILY_NAMES)}{rng.choice(KOREAN_GIVEN_NAMES)}"
    local_name = hashlib.sha256(name.encode()).hexdigest()[:10]
    return {
        "name": name,
        "email": f"{local_name}@{rng.choice(EMAIL_DOMAINS)}",
        "line1": f"{address['road']} {address['base'] + rng.randrange(address['span'])}",
        "line2": "",
        "city": "서울특별시",
        "state": str(address["district"]),
        "postal_code": str(address["postal"]),
        "country": provider_country,
    }


def kakao_proxy_chain(ctx: ExtractionContext, proxy_seed: str) -> tuple[str, str, str]:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if explicit_chain:
        return explicit_chain["checkout"], explicit_chain["promotion"], explicit_chain["provider"]
    checkout_proxy = proxy_for_country(proxy_seed, kakao_checkout_country(ctx))
    promotion_proxy = proxy_for_country(proxy_seed, kakao_promotion_country(ctx))
    provider_proxy = proxy_for_country(proxy_seed, kakao_provider_country(ctx))
    key = proxy_chain_key(proxy_seed)
    if key and any(proxy_chain_key(proxy) != key for proxy in (checkout_proxy, promotion_proxy, provider_proxy)):
        raise RuntimeError("proxy country rewrite changed sticky seed; refusing mixed Kakao chain")
    return checkout_proxy, promotion_proxy, provider_proxy


def log_kakao_proxy_chain(
    ctx: ExtractionContext,
    proxy_seed: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
) -> None:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    prefix = "explicit" if explicit_chain else "derived"
    ctx.log(
        f"Kakao proxy chain ({prefix}): "
        f"{kakao_checkout_country(ctx)} checkout={proxy_label(checkout_proxy)}; "
        f"{kakao_promotion_country(ctx)} promotion={proxy_label(promotion_proxy)}; "
        f"{kakao_provider_country(ctx)} provider/approve={proxy_label(provider_proxy)}"
    )


def stripe_headers(publishable_key: str, referer: str) -> dict[str, str]:
    origin = "https://checkout.stripe.com" if "checkout.stripe.com" in referer else "https://pay.openai.com"
    return {
        "Authorization": f"Bearer {publishable_key}",
        "Origin": origin,
        "Referer": referer,
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Sec-Fetch-Site": "same-site" if origin == "https://checkout.stripe.com" else "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": KAKAO_USER_AGENT,
    }


def elements_params(stripe_js_id: str, session_id: str = "") -> dict[str, str]:
    params = {
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": "ko",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "auto",
        "elements_options_client[saved_payment_method][enable_redisplay]": "auto",
    }
    if session_id:
        params["elements_session_client[session_id]"] = session_id
    return params


def checkout_processor_entity(checkout: dict[str, Any], provider_country: str = "KR") -> str:
    _ = provider_country
    return str(checkout.get("processor_entity") or "openai_llc")


def checkout_page_url(checkout_id: str, checkout: dict[str, Any], provider_country: str = "KR") -> str:
    return f"https://chatgpt.com/checkout/{checkout_processor_entity(checkout, provider_country)}/{checkout_id}"


def checkout_api_headers(token: str, referer: str, target_path: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "oai-language": "ko-KR",
        "User-Agent": KAKAO_USER_AGENT,
        "Referer": referer,
        "x-openai-target-path": target_path,
        "x-openai-target-route": target_path,
    }


def create_kakao_checkout(
    ctx: ExtractionContext,
    session: requests.Session,
    access_token: str,
) -> tuple[str, str, dict[str, Any]]:
    checkout_country = kakao_checkout_country(ctx)
    promo_mode = (_cfg_str(ctx, "promo_mode", "campaign", "KAKAO_PROMO_MODE") or "campaign").strip().lower()
    promo_id = (_cfg_str(ctx, "promo_id", "plus-1-month-free", "KAKAO_PROMO_ID") or "plus-1-month-free").strip()
    payload: dict[str, Any] = {
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": checkout_country, "currency": currency_for_country(checkout_country)},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "custom",
    }
    if promo_mode != "off" and promo_id:
        payload["promo_campaign"] = {"promo_campaign_id": promo_id, "is_coupon_from_query_param": False}

    url = "https://chatgpt.com/backend-api/payments/checkout"
    response = session.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "oai-language": "ko-KR",
            "User-Agent": KAKAO_USER_AGENT,
        },
        json=payload,
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(response, "kakao_checkout", payload, "POST", url, force=response.status_code >= 400)
    if response.status_code >= 400:
        if is_user_already_paid_error(response.text):
            raise RuntimeError("用户已支付: User is already paid")
        raise RuntimeError(f"Kakao checkout failed HTTP {response.status_code}: {response_error(response)}")

    checkout = response.json() or {}
    checkout_session = str(checkout.get("checkout_session_id") or checkout.get("session_id") or checkout.get("id") or "")
    publishable_key = str(
        checkout.get("publishable_key")
        or checkout.get("stripe_publishable_key")
        or checkout.get("publishableKey")
        or checkout.get("stripePublishableKey")
        or checkout.get("key")
        or ""
    )
    if not checkout_session or not checkout_session.startswith("cs_") or not publishable_key:
        raise RuntimeError(f"Kakao checkout missing cs/pk: {list(checkout.keys())}")
    ctx.log(f"Kakao checkout created: {checkout_session} / {checkout_country} / {currency_for_country(checkout_country)}")
    return checkout_session, publishable_key, checkout


def update_kakao_checkout_promotion(
    ctx: ExtractionContext,
    session: requests.Session,
    access_token: str,
    checkout_id: str,
    checkout: dict[str, Any],
) -> None:
    promotion_country = kakao_promotion_country(ctx)
    provider_country = kakao_provider_country(ctx)
    promo_mode = (_cfg_str(ctx, "promo_mode", "campaign", "KAKAO_PROMO_MODE") or "campaign").strip().lower()
    promo_id = (_cfg_str(ctx, "promo_id", "plus-1-month-free", "KAKAO_PROMO_ID") or "plus-1-month-free").strip()
    body: dict[str, Any] = {
        "checkout_session_id": checkout_id,
        "processor_entity": checkout_processor_entity(checkout, provider_country),
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
    }
    if promo_mode != "off" and promo_id:
        body["promo_campaign"] = {"promo_campaign_id": promo_id, "is_coupon_from_query_param": False}
    target_path = "/backend-api/payments/checkout/update"
    url = f"https://chatgpt.com{target_path}"
    response = session.post(
        url,
        headers=checkout_api_headers(access_token, checkout_page_url(checkout_id, checkout, provider_country), target_path),
        json=body,
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(response, "kakao_checkout_update", body, "POST", url, force=response.status_code >= 400)
    if response.status_code >= 400:
        if is_checkout_not_active_error(response.text):
            raise RuntimeError("checkout_not_active_session")
        raise RuntimeError(f"Kakao checkout/update failed HTTP {response.status_code}: {response_error(response)}")
    try:
        payload = response.json() or {}
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"Kakao checkout/update rejected: {str(payload)[:500]}")
    ctx.log(f"{promotion_country} checkout/update succeeded: promo={promo_id if 'promo_campaign' in body else 'off'}")


def update_kakao_checkout_taxes(
    ctx: ExtractionContext,
    session: requests.Session,
    access_token: str,
    checkout_id: str,
    checkout: dict[str, Any],
    billing: dict[str, str],
) -> None:
    provider_country = kakao_provider_country(ctx)
    target_path = "/backend-api/payments/checkout/taxes"
    body = {
        "checkout_session_id": checkout_id,
        "checkout_email": billing["email"],
        "billing_country": provider_country,
        "billing_name": billing["name"],
        "currency": currency_for_country(provider_country),
        "tax_id": None,
        "processor_entity": checkout_processor_entity(checkout, provider_country),
        "billing_address": {
            "line1": billing["line1"],
            "city": billing["city"],
            "country": provider_country,
            "postal_code": billing["postal_code"],
            "state": billing["state"],
        },
    }
    url = f"https://chatgpt.com{target_path}"
    response = session.post(
        url,
        headers=checkout_api_headers(access_token, checkout_page_url(checkout_id, checkout, provider_country), target_path),
        json=body,
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(response, "kakao_checkout_taxes", body, "POST", url, force=response.status_code >= 400)
    if response.status_code >= 400:
        raise RuntimeError(f"Kakao checkout/taxes failed HTTP {response.status_code}: {response_error(response)}")
    ctx.log(f"{provider_country} checkout/taxes synced")


def expected_amount(payload: dict[str, Any]) -> str:
    options = payload.get("elements_options") if isinstance(payload.get("elements_options"), dict) else {}
    if options.get("amount") is not None:
        return str(int(options["amount"]))
    total_summary = payload.get("total_summary") if isinstance(payload.get("total_summary"), dict) else {}
    if total_summary.get("due") is not None:
        return str(int(total_summary["due"]))
    invoice = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    for name in ("amount_due", "total"):
        if invoice.get(name) is not None:
            return str(int(invoice[name]))
    line_items = payload.get("line_items")
    if isinstance(line_items, list):
        amounts = [item.get("amount") for item in line_items if isinstance(item, dict) and item.get("amount") is not None]
        if amounts:
            return str(sum(int(value) for value in amounts))
    return "unknown"


def activate_stripe_checkout(ctx: ExtractionContext, session: requests.Session, checkout_id: str) -> str:
    checkout_page = f"https://checkout.stripe.com/c/pay/{checkout_id}"
    for url in (f"https://pay.openai.com/c/pay/{checkout_id}", checkout_page):
        try:
            response = session.get(
                url,
                headers={
                    "User-Agent": KAKAO_USER_AGENT,
                    "Accept": "text/html,*/*",
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
                    "Referer": "https://chatgpt.com/",
                },
                timeout=DEFAULT_TIMEOUT,
            )
            ctx.dump_http(response, "kakao_checkout_activate", {}, "GET", url, force=False)
        except Exception as exc:
            ctx.log(f"Kakao checkout activate failed: {str(exc)[:180]}", "[WARN] ")
    return checkout_page


def stripe_init(
    ctx: ExtractionContext,
    session: requests.Session,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
) -> tuple[dict[str, Any], str]:
    stripe_js_id = str(uuid.uuid4())
    body = {
        "key": publishable_key,
        "eid": "NA",
        "browser_locale": "ko-KR",
        "browser_timezone": "Asia/Seoul",
        "redirect_type": "url",
        "_stripe_version": STRIPE_VERSION,
        **elements_params(stripe_js_id),
    }
    url = f"https://api.stripe.com/v1/payment_pages/{checkout_id}/init"
    response = session.post(url, data=body, headers=stripe_headers(publishable_key, checkout_page), timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(response, "kakao_stripe_init", body, "POST", url, force=response.status_code >= 400)
    if response.status_code >= 400:
        raise RuntimeError(f"Kakao Stripe init failed HTTP {response.status_code}: {response_error(response)}")
    payload = response.json() or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Kakao Stripe init returned invalid payload")
    log_payment_page_summary(ctx, "kakao_stripe_init", payload)
    return payload, stripe_js_id


def inspect_kakao_init(ctx: ExtractionContext, payload: dict[str, Any], stage: str, require_zero: bool) -> str:
    amount = expected_amount(payload)
    currency = str(payload.get("currency") or first_value_by_key(payload, "currency") or "").lower()
    methods = [str(item).lower() for item in (payload.get("payment_method_types") or [])]
    if not methods:
        raw_methods = first_value_by_key(payload, "payment_method_types")
        if isinstance(raw_methods, list):
            methods = [str(item).lower() for item in raw_methods]
    ctx.log(f"{stage} Stripe init: amount={amount}; currency={currency}; methods={','.join(methods) or 'none'}")
    if "kakao_pay" not in methods or (require_zero and (amount != "0" or currency != "krw")):
        raise RuntimeError(f"checkout_not_kakao_trial: stage={stage} amount={amount} currency={currency} methods={methods}")
    return amount


def stripe_update_kakao_tax_region(
    ctx: ExtractionContext,
    session: requests.Session,
    checkout_id: str,
    publishable_key: str,
    checkout_page: str,
    stripe_js_id: str,
    elements_session_id: str,
    billing: dict[str, str],
) -> None:
    body = {
        "key": publishable_key,
        "_stripe_version": STRIPE_VERSION,
        **elements_params(stripe_js_id, elements_session_id),
        "tax_region[country]": billing["country"],
        "tax_region[postal_code]": billing["postal_code"],
        "tax_region[line1]": billing["line1"],
        "tax_region[city]": billing["city"],
        "tax_region[state]": billing["state"],
    }
    url = f"https://api.stripe.com/v1/payment_pages/{checkout_id}"
    response = session.post(url, data=body, headers=stripe_headers(publishable_key, checkout_page), timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(response, "kakao_tax_region", body, "POST", url, force=response.status_code >= 400)
    if response.status_code >= 400:
        raise RuntimeError(f"Kakao tax_region failed HTTP {response.status_code}: {response_error(response)}")
    ctx.log(f"{billing['country']} Stripe tax_region synced: {billing['city']} {billing['postal_code']}")


def extract_redirect(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    action = payload.get("next_action")
    if isinstance(action, dict) and action.get("type") == "redirect_to_url":
        redirect = action.get("redirect_to_url") or {}
        if isinstance(redirect, dict) and redirect.get("url"):
            return str(redirect["url"])
    for name in ("setup_intent", "payment_intent"):
        redirect = extract_redirect(payload.get(name))
        if redirect:
            return redirect
    return ""


def run_kakao_provider_flow(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
    stop_event: Event | None = None,
) -> str:
    provider_country = kakao_provider_country(ctx)
    approve_retry_max = _cfg_int(ctx, "kakao_approve_retry_max", 1, minimum=1, alias="KAKAO_APPROVE_RETRY_MAX")
    poll_timeout = _cfg_int(ctx, "kakao_poll_timeout", 120, minimum=30, alias="KAKAO_POLL_TIMEOUT")
    device_id = str(uuid.uuid4())
    checkout_session = new_session(ctx, checkout_proxy)
    promotion_session = new_session(ctx, promotion_proxy)
    provider_session = new_session(ctx, provider_proxy)
    for session in (checkout_session, promotion_session, provider_session):
        session.headers.update({"User-Agent": KAKAO_USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"})

    ensure_running(stop_event)
    ctx.log("checking ChatGPT token for Kakao")
    me = checkout_session.get(
        "https://chatgpt.com/backend-api/me",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": KAKAO_USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(me, "kakao_me", {}, "GET", "https://chatgpt.com/backend-api/me", force=me.status_code >= 400)
    if me.status_code != 200:
        raise RuntimeError(f"ChatGPT /me failed HTTP {me.status_code}: {response_error(me, 500)}")

    ensure_running(stop_event)
    ctx.log(f"{kakao_checkout_country(ctx)} creating KRW Kakao trial checkout")
    checkout_id, publishable_key, checkout = create_kakao_checkout(ctx, checkout_session, access_token)
    checkout_page = activate_stripe_checkout(ctx, checkout_session, checkout_id)

    ctx.log(f"{kakao_checkout_country(ctx)} Bootstrap Stripe init")
    bootstrap_payload, _ = stripe_init(ctx, checkout_session, checkout_id, publishable_key, checkout_page)
    inspect_kakao_init(ctx, bootstrap_payload, f"{kakao_checkout_country(ctx)} Bootstrap", require_zero=False)

    ensure_running(stop_event)
    ctx.log(f"{kakao_promotion_country(ctx)} checkout/update")
    update_kakao_checkout_promotion(ctx, promotion_session, access_token, checkout_id, checkout)

    ensure_running(stop_event)
    ctx.log(f"{kakao_promotion_country(ctx)} checkout/update then refresh Stripe through {provider_country}")
    init_payload, stripe_js_id = stripe_init(ctx, provider_session, checkout_id, publishable_key, checkout_page)
    amount = inspect_kakao_init(ctx, init_payload, f"{kakao_promotion_country(ctx)} updated {provider_country}", require_zero=True)

    billing = random_kakao_billing(ctx, access_token)
    tax_elements_session_id = f"elements_session_{uuid.uuid4().hex[:11]}"
    ensure_running(stop_event)
    ctx.log(f"syncing {provider_country} checkout/taxes and Stripe tax_region")
    update_kakao_checkout_taxes(ctx, provider_session, access_token, checkout_id, checkout, billing)
    stripe_update_kakao_tax_region(
        ctx,
        provider_session,
        checkout_id,
        publishable_key,
        checkout_page,
        stripe_js_id,
        tax_elements_session_id,
        billing,
    )

    ensure_running(stop_event)
    ctx.log(f"{provider_country} refresh Stripe after taxes")
    init_payload, stripe_js_id = stripe_init(ctx, provider_session, checkout_id, publishable_key, checkout_page)
    amount = inspect_kakao_init(ctx, init_payload, f"{provider_country} tax synced", require_zero=True)
    elements_session_id = f"elements_session_{uuid.uuid4().hex[:11]}"

    ensure_running(stop_event)
    ctx.log(f"{provider_country} Stripe pre_confirm Kakao")
    pre_confirm_body = {
        "eid": str(uuid.uuid4()),
        "payment_method_type": "kakao_pay",
        "key": publishable_key,
        "_stripe_version": STRIPE_VERSION,
    }
    pre_confirm_url = f"https://api.stripe.com/v1/payment_pages/{checkout_id}/pre_confirm"
    pre_confirm = provider_session.post(
        pre_confirm_url,
        data=pre_confirm_body,
        headers=stripe_headers(publishable_key, checkout_page),
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(pre_confirm, "kakao_pre_confirm", pre_confirm_body, "POST", pre_confirm_url, force=pre_confirm.status_code >= 400)
    if pre_confirm.status_code >= 400:
        raise RuntimeError(f"Kakao pre_confirm failed HTTP {pre_confirm.status_code}: {response_error(pre_confirm)}")

    ensure_running(stop_event)
    ctx.log(f"{provider_country} creating Kakao payment_method")
    client_session_id = str(uuid.uuid4())
    guid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    muid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    sid = f"{uuid.uuid4()}{os.urandom(3).hex()}"
    payment_method_body = {
        "type": "kakao_pay",
        "billing_details[name]": billing["name"],
        "billing_details[email]": billing["email"],
        "billing_details[address][country]": provider_country,
        "billing_details[address][line1]": billing["line1"],
        "billing_details[address][line2]": billing["line2"],
        "billing_details[address][city]": billing["city"],
        "billing_details[address][postal_code]": billing["postal_code"],
        "billing_details[address][state]": billing["state"],
        "guid": guid,
        "muid": muid,
        "sid": sid,
        "_stripe_version": STRIPE_VERSION,
        "key": publishable_key,
        "payment_user_agent": STRIPE_PAYMENT_UA,
        "client_attribution_metadata[client_session_id]": client_session_id,
        "client_attribution_metadata[checkout_session_id]": checkout_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
    }
    config_id = str(init_payload.get("config_id") or "")
    if config_id:
        payment_method_body["client_attribution_metadata[checkout_config_id]"] = config_id
    pm_url = "https://api.stripe.com/v1/payment_methods"
    payment_method_response = provider_session.post(
        pm_url,
        data=payment_method_body,
        headers=stripe_headers(publishable_key, checkout_page),
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(payment_method_response, "kakao_pm", payment_method_body, "POST", pm_url, force=payment_method_response.status_code >= 400)
    if payment_method_response.status_code >= 400:
        raise RuntimeError(f"Kakao payment method failed HTTP {payment_method_response.status_code}: {response_error(payment_method_response, 1000)}")
    payment_method_id = str((payment_method_response.json() or {}).get("id") or "")
    if not payment_method_id.startswith("pm_"):
        raise RuntimeError(f"Kakao payment method missing id: {response_error(payment_method_response, 500)}")

    ensure_running(stop_event)
    ctx.log(f"{provider_country} Stripe confirm")
    processor_entity = checkout_processor_entity(checkout, provider_country)
    success_url = (
        f"https://chatgpt.com/backend-api/payments/checkout/{processor_entity}/{checkout_id}/success?"
        f"billing_country={provider_country}"
    )
    return_url = (
        f"https://checkout.stripe.com/c/pay/{checkout_id}?returned_from_redirect=true&ui_mode=custom&"
        f"return_url={quote(success_url, safe='')}"
    )
    confirm_body = {
        "eid": "NA",
        "payment_method": payment_method_id,
        "expected_amount": amount,
        "tax_id_collection[purchasing_as_business]": "false",
        "expected_payment_method_type": "kakao_pay",
        "return_url": return_url,
        "_stripe_version": STRIPE_VERSION,
        "guid": guid,
        "muid": muid,
        "sid": sid,
        "key": publishable_key,
        "version": STRIPE_RUNTIME,
        "init_checksum": str(init_payload.get("init_checksum") or ""),
        "client_attribution_metadata[client_session_id]": client_session_id,
        "client_attribution_metadata[checkout_session_id]": checkout_id,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
        "link_brand": "link",
        **elements_params(stripe_js_id, elements_session_id),
    }
    if config_id:
        confirm_body["client_attribution_metadata[checkout_config_id]"] = config_id
    confirm_url = f"https://api.stripe.com/v1/payment_pages/{checkout_id}/confirm"
    confirm_response = provider_session.post(
        confirm_url,
        data=confirm_body,
        headers=stripe_headers(publishable_key, checkout_page),
        timeout=DEFAULT_TIMEOUT,
    )
    ctx.dump_http(confirm_response, "kakao_confirm", confirm_body, "POST", confirm_url, force=True)
    if confirm_response.status_code >= 400:
        raise RuntimeError(f"Kakao confirm failed HTTP {confirm_response.status_code}: {response_error(confirm_response, 1000)}")
    confirm_payload = confirm_response.json() or {}
    log_payment_page_summary(ctx, "kakao_confirm", confirm_payload)
    redirect = extract_redirect(confirm_payload)
    submission = confirm_payload.get("submission_attempt") if isinstance(confirm_payload.get("submission_attempt"), dict) else {}

    if not redirect and (submission.get("state") == "requires_approval" or checkout.get("requires_manual_approval")):
        ctx.log(f"{provider_country} OpenAI approve, max={approve_retry_max}")
        approve_session = new_session(ctx, provider_proxy)
        approve_session.headers.update({"User-Agent": KAKAO_USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"})
        cookie = f"oai-did={device_id}"
        if session_token:
            cookie += f"; __Secure-next-auth.session-token={session_token}"
        last_error = ""
        for index in range(1, approve_retry_max + 1):
            ensure_running(stop_event)
            approval_body = {"checkout_session_id": checkout_id, "processor_entity": processor_entity}
            approval_url = "https://chatgpt.com/backend-api/payments/checkout/approve"
            approval_response = approve_session.post(
                approval_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "oai-language": "ko-KR",
                    "User-Agent": KAKAO_USER_AGENT,
                    "Referer": checkout_page_url(checkout_id, checkout, provider_country),
                    "Cookie": cookie,
                },
                json=approval_body,
                timeout=DEFAULT_TIMEOUT,
            )
            ctx.dump_http(approval_response, "kakao_approve", approval_body, "POST", approval_url, force=True)
            if approval_response.status_code == 200:
                try:
                    if (approval_response.json() or {}).get("result") == "approved":
                        ctx.log(f"{provider_country} approve succeeded on attempt {index}")
                        last_error = ""
                        break
                except Exception:
                    pass
            last_error = f"Kakao approve failed HTTP {approval_response.status_code}: {response_error(approval_response, 500)}"
            if index < approve_retry_max:
                time.sleep(1)
        if last_error:
            raise RuntimeError(last_error)

    ctx.log(f"{provider_country} polling Stripe redirect, timeout={poll_timeout}s")
    poll_params = {"key": publishable_key, **elements_params(stripe_js_id, elements_session_id)}
    deadline = time.time() + poll_timeout
    while not redirect and time.time() < deadline:
        ensure_running(stop_event)
        poll_url = f"https://api.stripe.com/v1/payment_pages/{checkout_id}"
        poll_response = provider_session.get(
            poll_url,
            params=poll_params,
            headers=stripe_headers(publishable_key, checkout_page),
            timeout=8,
        )
        if poll_response.status_code == 200:
            redirect = extract_redirect(poll_response.json() or {})
        if not redirect:
            time.sleep(1)
    if not redirect:
        raise RuntimeError("Kakao redirect url timeout")

    current = redirect
    for _ in range(6):
        ensure_running(stop_event)
        host = urlsplit(current).netloc.lower()
        if "nicepay" in host or "kakao" in host:
            break
        response = provider_session.get(current, allow_redirects=False, timeout=DEFAULT_TIMEOUT)
        location = str(response.headers.get("Location") or "")
        if response.status_code not in {301, 302, 303, 307, 308} or not location:
            break
        current = urljoin(current, location)
    final_host = urlsplit(current).netloc.lower()
    if "nicepay" not in final_host and "kakao" not in final_host:
        raise RuntimeError(f"not kakao/nicepay redirect: {current[:180]}")
    return current


def _pick_seed_candidates(ctx: ExtractionContext, proxy_seeds: list[str], limit: int) -> list[str]:
    ordered = ctx.order_proxy_group("seed", proxy_seeds)
    if limit >= len(ordered):
        return ordered
    return random.sample(ordered, min(limit, len(ordered)))


def is_account_error(reason: str) -> bool:
    text = str(reason or "").lower()
    return any(
        marker in text
        for marker in (
            "invalid access token",
            "token_invalidated",
            "authentication token has been invalidated",
            "chatgpt /me failed http 401",
            "kakao checkout failed http 401",
            "checkout/update failed http 401",
            "checkout/taxes failed http 401",
            "approve failed http 401",
            "token expired",
            "already paid",
            "already subscribed",
            "already has plus",
            "active subscription",
        )
    )


def run_kakao_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
    attempt: int,
    kakao_retry: int,
    checkout_retry: int,
    stop_event: Event,
) -> tuple[int, str, str]:
    previous_log_context = getattr(ctx.log_context, "prefix", "")
    ctx.log_context.prefix = f"[Kakao {attempt}/{kakao_retry}] "
    last_error = ""
    checkout_proxy = ""
    promotion_proxy = ""
    provider_proxy = ""
    try:
        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt"
        candidates = _pick_seed_candidates(ctx, proxy_seeds, checkout_retry)
        ctx.log(
            f"starting Kakao extraction {attempt}/{kakao_retry}: "
            f"{kakao_checkout_country(ctx)}/{currency_for_country(kakao_checkout_country(ctx))} -> "
            f"{kakao_promotion_country(ctx)} -> {kakao_provider_country(ctx)}"
        )
        for index, proxy_seed in enumerate(candidates, start=1):
            if stop_event.is_set():
                return attempt, "", "task stopped, skipping attempt"
            try:
                checkout_proxy, promotion_proxy, provider_proxy = kakao_proxy_chain(ctx, proxy_seed)
                log_kakao_proxy_chain(ctx, proxy_seed, checkout_proxy, promotion_proxy, provider_proxy)
                ctx.log(f"Kakao chain {index}/{len(candidates)}: checkout={proxy_label(checkout_proxy)}")
                final_url = run_kakao_provider_flow(
                    ctx,
                    access_token,
                    session_token,
                    checkout_proxy,
                    promotion_proxy,
                    provider_proxy,
                    stop_event,
                )
                ctx.record_proxy_pair_result(checkout_proxy, provider_proxy, True, "kakao_success")
                ctx.record_proxy_result("promotion", promotion_proxy, True, "kakao_success")
                stop_event.set()
                return attempt, final_url, ""
            except TaskStopped:
                return attempt, "", "task stopped"
            except Exception as exc:
                error = str(exc)
                last_error = error
                if is_account_error(error):
                    ctx.log(f"Kakao account cannot continue: {error[:220]}", "[ERROR] ")
                    stop_event.set()
                    return attempt, "", error
                ctx.record_failure_by_stage(error, checkout_proxy, provider_proxy)
                if promotion_proxy:
                    ctx.record_proxy_result("promotion", promotion_proxy, False, error)
                ctx.log(f"Kakao chain failed: {error[:260]}", "[WARN] ")
        return attempt, "", last_error or "kakao_failed"
    finally:
        ctx.log_context.prefix = previous_log_context


def run_kakao_single_link_parallel_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="KAKAO_SEEDS_PER_ROUND")
    kakao_retry = _cfg_int(ctx, "max_retry", 5, alias="KAKAO_MAX_RETRY")
    requested_workers = _cfg_int(ctx, "workers", 1, alias="KAKAO_WORKERS")
    worker_limit = _cfg_int(ctx, "workers_max", requested_workers, alias="KAKAO_WORKERS_MAX")
    workers = min(max(1, requested_workers), max(1, worker_limit), kakao_retry)
    stop_event = Event()
    last_error = ""
    ctx.log(
        "starting Kakao extraction: "
        f"proxy_chain={kakao_checkout_country(ctx)}/{kakao_promotion_country(ctx)}/{kakao_provider_country(ctx)}, "
        f"checkout_retry={checkout_retry}, kakao_retry={kakao_retry}, workers={workers}."
    )
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Any, int] = {}
    try:
        for attempt in range(1, kakao_retry + 1):
            futures[
                executor.submit(
                    run_kakao_attempt,
                    ctx,
                    access_token,
                    session_token,
                    proxy_seeds,
                    attempt,
                    kakao_retry,
                    checkout_retry,
                    stop_event,
                )
            ] = attempt
        for future in as_completed(futures):
            attempt = futures.get(future, 0)
            try:
                _attempt, final_url, error = future.result()
            except Exception as exc:
                final_url = ""
                error = str(exc)
                ctx.log(f"Kakao extraction {attempt}/{kakao_retry} exception: {error[:300]}", "[WARN] ")
            if final_url:
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                ctx.log(f"Kakao final payment URL: {final_url}")
                print("\n===== result =====")
                print(f"Kakao final payment URL:\n{final_url}")
                return 0
            last_error = error or last_error
            if is_account_error(error):
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                return 1
    finally:
        executor.shutdown(wait=True, cancel_futures=stop_event.is_set())
    ctx.log(f"all Kakao attempts failed: {last_error}", "[ERROR] ")
    return 1


def run_kakao_single_link_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    kakao_workers = _cfg_int(ctx, "workers", 1, alias="KAKAO_WORKERS")
    if kakao_workers > 1:
        return run_kakao_single_link_parallel_mode(ctx, access_token, session_token, proxy_seeds)

    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="KAKAO_SEEDS_PER_ROUND")
    kakao_retry = _cfg_int(ctx, "max_retry", 5, alias="KAKAO_MAX_RETRY")
    stop_event = Event()
    attempted_seed_keys: set[str] = set()
    last_error = ""
    ctx.log(
        "starting Kakao extraction: "
        f"proxy_chain={kakao_checkout_country(ctx)}/{kakao_promotion_country(ctx)}/{kakao_provider_country(ctx)}, "
        f"checkout_retry={checkout_retry}, kakao_retry={kakao_retry}."
    )
    for attempt in range(1, kakao_retry + 1):
        available_seeds = [
            proxy_seed
            for proxy_seed in proxy_seeds
            if proxy_chain_key(proxy_seed) not in attempted_seed_keys
        ]
        if not available_seeds:
            last_error = last_error or "all proxy seeds attempted"
            ctx.log("all proxy seeds have been attempted for this Kakao task", "[WARN] ")
            break
        candidates = _pick_seed_candidates(ctx, available_seeds, checkout_retry)
        for proxy_seed in candidates:
            attempted_seed_keys.add(proxy_chain_key(proxy_seed))
        _attempt, final_url, error = run_kakao_attempt(
            ctx,
            access_token,
            session_token,
            candidates,
            attempt,
            kakao_retry,
            checkout_retry,
            stop_event,
        )
        if final_url:
            ctx.log(f"Kakao final payment URL: {final_url}")
            print("\n===== result =====")
            print(f"Kakao final payment URL:\n{final_url}")
            return 0
        last_error = error or last_error
        if is_account_error(error):
            return 1
        ctx.log(f"Kakao extraction {attempt}/{kakao_retry} ended without final URL", "[WARN] ")
        time.sleep(0.5)
    ctx.log(f"all Kakao attempts failed: {last_error}", "[ERROR] ")
    return 1
