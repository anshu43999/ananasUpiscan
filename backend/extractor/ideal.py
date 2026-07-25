"""iDEAL extraction orchestration.

This module follows the JP -> NL iDEAL flow from the reference extractor while
reusing the local checkout/session/provider helpers already used by UPI.
"""

from __future__ import annotations

import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Any

import requests

from .checkout import (
    STRIPE_VERSION_FULL,
    amount_from_payload,
    build_chatgpt_session,
    create_checkout,
    is_checkout_not_active_error,
    is_user_already_paid_error,
    random_user_agent,
    stripe_init,
)
from .config import DEFAULT_STRIPE_PK, DEFAULT_TIMEOUT
from .context import ExtractionContext
from .provider import (
    approve_proxy_candidates,
    approve_with_retry,
    build_ctx,
    extract_redirect_url,
    find_submission_attempt,
    first_value_by_key,
    infer_processor_entity,
    log_payment_page_summary,
    poll_payment_page,
    random_runtime_version,
    resolve_external_redirect,
    should_retry_second_confirm_after_approve,
    stripe_browser_id,
    stripe_confirm_return_url,
    stripe_payload_intent_redirect_url,
    stripe_update_customer_data,
    stripe_update_tax_region,
)
from .proxy import (
    currency_for_country,
    normalize_country,
    parse_proxy_chain_seed,
    proxy_chain_key,
    proxy_for_country,
    proxy_label,
)
from .session import new_session


NETHERLANDS_BILLING_NAMES: list[tuple[str, str]] = [
    ("Jan", "de Vries"),
    ("Sanne", "Jansen"),
    ("Daan", "Bakker"),
    ("Emma", "Visser"),
    ("Lars", "Smit"),
]

NETHERLANDS_BILLING_STREETS: list[tuple[str, str, str, str]] = [
    ("Prinsengracht 263", "Amsterdam", "", "1016 GV"),
    ("Coolsingel 40", "Rotterdam", "", "3011 AD"),
    ("Oudegracht 120", "Utrecht", "", "3511 AW"),
    ("Grote Markt 1", "Groningen", "", "9712 HN"),
]


def _cfg_bool(ctx: ExtractionContext, primary: str, default: bool = False, alias: str = "") -> bool:
    value = ctx.cfg_bool(primary, default)
    return ctx.cfg_bool(alias, value) if alias else value


def _cfg_int(ctx: ExtractionContext, primary: str, default: int, minimum: int = 1, alias: str = "") -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: ExtractionContext, primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def _payment_browser_locale(ctx: ExtractionContext) -> str:
    return _cfg_str(ctx, "browser_locale", "nl-NL", "IDEAL_BROWSER_LOCALE") or "nl-NL"


def ideal_billing_profile(ctx: ExtractionContext) -> dict[str, str]:
    first_name, last_name = random.choice(NETHERLANDS_BILLING_NAMES)
    line1, city, state, postal_code = random.choice(NETHERLANDS_BILLING_STREETS)
    suffix = random.randint(1000, 9999)
    profile = {
        "email": f"{first_name.lower()}.{last_name.lower().replace(' ', '')}{suffix}@example.com",
        "name": f"{first_name} {last_name}",
        "country": "NL",
        "line1": line1,
        "line2": "",
        "city": city,
        "postal_code": postal_code,
        "state": state,
    }
    if _cfg_bool(ctx, "use_fixed_billing", False, "IDEAL_USE_FIXED_BILLING"):
        profile.update(
            {
                "email": _cfg_str(ctx, "billing_email", profile["email"], "IDEAL_EMAIL"),
                "name": _cfg_str(ctx, "billing_name", profile["name"], "IDEAL_NAME"),
                "country": _cfg_str(ctx, "billing_country", "NL", "IDEAL_BILLING_COUNTRY"),
                "line1": _cfg_str(ctx, "billing_line1", profile["line1"], "IDEAL_LINE1"),
                "line2": _cfg_str(ctx, "billing_line2", "", "IDEAL_LINE2"),
                "city": _cfg_str(ctx, "billing_city", profile["city"], "IDEAL_CITY"),
                "postal_code": _cfg_str(ctx, "billing_postal_code", profile["postal_code"], "IDEAL_POSTAL_CODE"),
                "state": _cfg_str(ctx, "billing_state", profile["state"], "IDEAL_STATE"),
            }
        )
    profile["country"] = normalize_country(profile.get("country", "NL"))
    return {key: str(value) for key, value in profile.items()}


def stripe_create_ideal_pm(
    ctx: ExtractionContext,
    stripe: requests.Session,
    cs_id: str,
    stripe_pk: str,
    billing: dict[str, str],
) -> str:
    body: dict[str, Any] = {
        "billing_details[name]": billing.get("name") or "Jan de Vries",
        "billing_details[email]": billing.get("email") or "buyer@example.com",
        "billing_details[address][country]": billing.get("country") or "NL",
        "billing_details[address][line1]": billing.get("line1") or "Prinsengracht 263",
        "billing_details[address][city]": billing.get("city") or "Amsterdam",
        "billing_details[address][postal_code]": billing.get("postal_code") or "1016 GV",
        "type": "ideal",
        "client_attribution_metadata[checkout_session_id]": cs_id,
        "key": stripe_pk,
    }
    url = "https://api.stripe.com/v1/payment_methods"
    resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "ideal_pm", body, "POST", url, force=resp.status_code >= 400)
    if resp.status_code >= 400:
        raise RuntimeError(f"create iDEAL PM failed HTTP {resp.status_code}: {resp.text[:500]}")
    pm_id = str((resp.json() or {}).get("id") or "")
    if not pm_id.startswith("pm_"):
        raise RuntimeError(f"create iDEAL PM response invalid: {resp.text[:300]}")
    return pm_id


def stripe_confirm_ideal(
    ctx: ExtractionContext,
    stripe: requests.Session,
    cs_id: str,
    pm_id: str,
    stripe_pk: str,
    init_payload: dict[str, Any],
    stripe_ctx: dict[str, Any],
    checkout: dict[str, str],
    stripe_hosted_url: str,
) -> dict[str, Any]:
    runtime_version = str(stripe_ctx.get("runtime_version") or random_runtime_version(ctx))
    body: dict[str, Any] = {
        "eid": "NA",
        "payment_method": pm_id,
        "expected_amount": _cfg_str(ctx, "expected_amount", "", "PP_EXPECTED_AMOUNT")
        or str(stripe_ctx.get("checkout_amount") or amount_from_payload(init_payload)),
        "expected_payment_method_type": "ideal",
        "return_url": stripe_confirm_return_url(cs_id, checkout, stripe_hosted_url),
        "_stripe_version": str(stripe_ctx.get("stripe_version") or STRIPE_VERSION_FULL),
        "guid": str(stripe_ctx.get("guid") or stripe_browser_id()),
        "muid": str(stripe_ctx.get("muid") or stripe_browser_id()),
        "sid": str(stripe_ctx.get("sid") or stripe_browser_id()),
        "key": stripe_pk,
        "version": runtime_version,
        "init_checksum": str(init_payload.get("init_checksum") or stripe_ctx.get("init_checksum") or ""),
        "client_attribution_metadata[client_session_id]": str(
            stripe_ctx.get("client_session_id") or stripe_ctx["stripe_js_id"]
        ),
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
    url = f"https://api.stripe.com/v1/payment_pages/{cs_id}/confirm"
    resp = stripe.post(url, data=body, timeout=DEFAULT_TIMEOUT)
    ctx.dump_http(resp, "ideal_confirm", body, "POST", url, force=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"iDEAL confirm failed HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json() or {}


def ideal_proxy_chain(ctx: ExtractionContext, proxy_seed: str) -> tuple[str, str]:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if explicit_chain:
        return explicit_chain["checkout"], explicit_chain["provider"]
    checkout_proxy = proxy_for_country(proxy_seed, _cfg_str(ctx, "ideal_checkout_proxy_country", "JP", "IDEAL_CHECKOUT_PROXY_COUNTRY"))
    provider_proxy = proxy_for_country(proxy_seed, _cfg_str(ctx, "ideal_provider_proxy_country", "NL", "IDEAL_PROVIDER_PROXY_COUNTRY"))
    return checkout_proxy, provider_proxy


def log_ideal_proxy_chain(
    ctx: ExtractionContext,
    proxy_seed: str,
    checkout_proxy: str,
    provider_proxy: str,
) -> None:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    prefix = "explicit" if explicit_chain else "derived"
    ctx.log(
        f"iDEAL proxy chain ({prefix}): "
        f"JP checkout={proxy_label(checkout_proxy)}; NL provider/approve={proxy_label(provider_proxy)}"
    )


def _validate_ideal_init(ctx: ExtractionContext, init_payload: dict[str, Any], checkout: dict[str, str]) -> dict[str, Any]:
    stripe_ctx = build_ctx(ctx, init_payload, checkout)
    amount = int(stripe_ctx.get("checkout_amount") or 0)
    amount_major = amount / 100
    ctx.log(f"iDEAL Stripe init succeeded, amount={checkout['currency']} {amount_major:.2f}")
    payment_method_types = first_value_by_key(init_payload, "payment_method_types")
    if isinstance(payment_method_types, list):
        methods = [str(item).lower() for item in payment_method_types]
        ctx.log(f"Stripe payment methods: {methods}")
        if "ideal" not in methods:
            raise RuntimeError(f"iDEAL_unavailable: payment_method_types={methods}")
    max_amount = _cfg_int(ctx, "ideal_max_minor_amount", 50, minimum=0, alias="IDEAL_MAX_MINOR_AMOUNT")
    if amount > max_amount:
        raise RuntimeError(f"iDEAL amount policy failed: amount minor={amount}, max={max_amount}")
    ctx.log(f"iDEAL amount policy accepted: minor={amount}, max={max_amount}")
    return stripe_ctx


def resolve_confirm_payload_ideal(
    ctx: ExtractionContext,
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
) -> tuple[str, str]:
    redirect_url = extract_redirect_url(confirm_payload)
    if not redirect_url:
        redirect_url = stripe_payload_intent_redirect_url(ctx, stripe, confirm_payload, stripe_pk, current_pm_id=pm_id)
    submission = find_submission_attempt(confirm_payload)
    approve_proxy = ""

    if redirect_url:
        ctx.log(f"confirm found iDEAL payment URL: {redirect_url[:180]}")
    elif submission.get("state") == "requires_approval":
        ctx.log("ChatGPT approve required for iDEAL")
        approve_proxies = approve_proxy_candidates(ctx, checkout_proxy, provider_proxy, approve_pool)
        approve_proxy = approve_with_retry(ctx, access_token, device_id, checkout, approve_proxies, session_token, "provider")
        ctx.log("polling iDEAL redirect after approve")
        redirect_url, _qr_urls = poll_payment_page(ctx, stripe, checkout, stripe_pk, stripe_ctx, current_pm_id=pm_id)
    else:
        ctx.log("confirm did not return iDEAL redirect; polling payment_pages", "[WARN] ")
        redirect_url, _qr_urls = poll_payment_page(ctx, stripe, checkout, stripe_pk, stripe_ctx, current_pm_id=pm_id)

    return redirect_url, approve_proxy


def run_ideal_provider_flow(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
    device_id: str,
    checkout: dict[str, str],
    billing: dict[str, str],
    stop_event: Event | None = None,
) -> str:
    stripe_pk = checkout.get("stripe_pk") or DEFAULT_STRIPE_PK
    ctx.log(f"iDEAL Stripe init through NL provider: proxy={proxy_label(provider_proxy)}")
    init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
    if not checkout.get("processor_entity"):
        processor_entity = infer_processor_entity(init_payload)
        if processor_entity:
            checkout["processor_entity"] = processor_entity
            ctx.log(f"inferred processor_entity={processor_entity} from Stripe init")
    hosted_url = str(init_payload.get("stripe_hosted_url") or "")
    stripe_ctx = _validate_ideal_init(ctx, init_payload, checkout)
    if stop_event and stop_event.is_set():
        raise RuntimeError("task stopped, skipping attempt")

    stripe = new_session(ctx, provider_proxy)
    stripe.headers.update({"User-Agent": random_user_agent(), "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8"})

    if _cfg_bool(ctx, "update_tax_region", False, "IDEAL_UPDATE_TAX_REGION"):
        ctx.log("syncing NL Stripe tax_region...")
        stripe_update_tax_region(ctx, stripe, checkout["cs_id"], stripe_pk, stripe_ctx, billing)

    ctx.log(f"creating PM (iDEAL): {billing['country']} {billing['name']} / {billing['city']}")
    pm_id = stripe_create_ideal_pm(ctx, stripe, checkout["cs_id"], stripe_pk, billing)
    ctx.log(f"PM created: {pm_id}")

    if _cfg_bool(ctx, "update_customer_data", False, "IDEAL_UPDATE_CUSTOMER_DATA"):
        ctx.log(f"submitting customer data: {billing['name']} / {billing['city']} {billing['postal_code']}")
        stripe_update_customer_data(ctx, stripe, checkout["cs_id"], stripe_pk, stripe_ctx, billing)

    ctx.log("Stripe confirm (expected=iDEAL)...")
    confirm_payload = stripe_confirm_ideal(
        ctx, stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, stripe_ctx, checkout, hosted_url
    )
    ctx.log("Stripe confirm succeeded, parsing iDEAL redirect...")
    log_payment_page_summary(ctx, "ideal_confirm", confirm_payload)

    approve_proxy = ""
    try:
        redirect_url, approve_proxy = resolve_confirm_payload_ideal(
            ctx,
            stripe,
            confirm_payload,
            checkout,
            stripe_pk,
            stripe_ctx,
            pm_id,
            access_token,
            device_id,
            session_token,
            checkout_proxy,
            provider_proxy,
            approve_pool,
        )
    except Exception as exc:
        if not should_retry_second_confirm_after_approve(exc):
            raise
        ctx.log(f"no iDEAL redirect after approve/confirm; refreshing init for second confirm: {str(exc)[:180]}", "[WARN] ")
        init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
        hosted_url = str(init_payload.get("stripe_hosted_url") or hosted_url or "")
        stripe_ctx = build_ctx(ctx, init_payload, checkout)
        confirm_payload = stripe_confirm_ideal(
            ctx, stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, stripe_ctx, checkout, hosted_url
        )
        log_payment_page_summary(ctx, "ideal_second_confirm", confirm_payload)
        redirect_url, retry_approve_proxy = resolve_confirm_payload_ideal(
            ctx,
            stripe,
            confirm_payload,
            checkout,
            stripe_pk,
            stripe_ctx,
            pm_id,
            access_token,
            device_id,
            session_token,
            checkout_proxy,
            provider_proxy,
            approve_pool,
        )
        approve_proxy = retry_approve_proxy or approve_proxy

    if redirect_url and approve_proxy:
        ctx.record_proxy_pair_approve_success(checkout_proxy, provider_proxy, approve_proxy)

    if redirect_url:
        final_url = resolve_external_redirect(ctx, stripe, redirect_url)
        if final_url and final_url != redirect_url:
            ctx.log(f"resolved final iDEAL redirect: {final_url[:180]}")
            redirect_url = final_url
    return redirect_url


def _pick_seed_candidates(ctx: ExtractionContext, proxy_seeds: list[str], limit: int) -> list[str]:
    ordered = ctx.order_proxy_group("seed", proxy_seeds)
    if limit >= len(ordered):
        return ordered
    return random.sample(ordered, min(limit, len(ordered)))


def run_ideal_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
    attempt: int,
    ideal_retry: int,
    checkout_retry: int,
    checkout_country: str,
    checkout_currency: str,
    stop_event: Event,
) -> tuple[int, str, str]:
    previous_log_context = getattr(ctx.log_context, "prefix", "")
    ctx.log_context.prefix = f"[iDEAL {attempt}/{ideal_retry}] "
    last_error = ""
    checkout_proxy_used = ""
    provider_proxy = ""
    try:
        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt"
        billing = ideal_billing_profile(ctx)
        device_id = str(uuid.uuid4())
        checkout: dict[str, str] | None = None
        checkout_candidates = _pick_seed_candidates(ctx, proxy_seeds, checkout_retry)

        ctx.log(f"starting iDEAL extraction {attempt}/{ideal_retry}")
        ctx.log(
            f"Step 1: create ChatGPT checkout, checkout billing={checkout_country}/{checkout_currency}, "
            f"sampling up to {checkout_retry} seed nodes"
        )
        ctx.log(f"PM country: {billing['country']}")

        for checkout_index, proxy_seed in enumerate(checkout_candidates, start=1):
            if stop_event.is_set():
                return attempt, "", "task stopped, skipping attempt"
            try:
                checkout_proxy, provider_proxy = ideal_proxy_chain(ctx, proxy_seed)
                log_ideal_proxy_chain(ctx, proxy_seed, checkout_proxy, provider_proxy)
                ctx.log(
                    f"Checkout {checkout_index}/{len(checkout_candidates)}: "
                    f"{checkout_country}/{checkout_currency}, proxy={proxy_label(checkout_proxy)}"
                )
                chatgpt = build_chatgpt_session(ctx, access_token, device_id, checkout_proxy, session_token)
                checkout = create_checkout(ctx, chatgpt, checkout_country)
                checkout_proxy_used = checkout_proxy
                break
            except Exception as exc:
                error = str(exc)
                last_error = error
                if is_user_already_paid_error(error):
                    ctx.log("detected User is already paid; stopping task")
                    stop_event.set()
                    return attempt, "", error
                if not is_checkout_not_active_error(error):
                    ctx.record_failure_by_stage(f"checkout stage failed: {error}", proxy_seed, "")
                ctx.log(f"Checkout {checkout_index}/{len(checkout_candidates)} failed: {error[:220]}", "[WARN] ")

        if not checkout or not checkout_proxy_used:
            return attempt, "", last_error or "checkout_failed"

        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt"
        redirect_url = run_ideal_provider_flow(
            ctx,
            access_token,
            session_token,
            checkout_proxy_used,
            provider_proxy,
            [provider_proxy],
            device_id,
            checkout,
            billing,
            stop_event,
        )
        if redirect_url:
            ctx.record_proxy_pair_result(checkout_proxy_used, provider_proxy, True, "success")
            stop_event.set()
            return attempt, redirect_url, ""
        ctx.record_proxy_result("provider", provider_proxy, False, "no_redirect_url")
        return attempt, "", "no_redirect_url"
    except Exception as exc:
        error = str(exc)
        last_error = error
        if checkout_proxy_used or provider_proxy:
            ctx.record_failure_by_stage(error, checkout_proxy_used, provider_proxy)
        ctx.log(f"iDEAL provider failed: {error[:220]}", "[WARN] ")
        return attempt, "", last_error
    finally:
        ctx.log_context.prefix = previous_log_context


def run_ideal_single_link_parallel_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="IDEAL_CHECKOUT_RETRY_MAX")
    ideal_retry = _cfg_int(ctx, "max_retry", 5, alias="IDEAL_MAX_RETRY")
    requested_workers = _cfg_int(ctx, "workers", 1, alias="IDEAL_WORKERS")
    worker_limit = _cfg_int(ctx, "workers_max", requested_workers, alias="IDEAL_WORKERS_MAX")
    workers = min(max(1, requested_workers), max(1, worker_limit), ideal_retry)
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", "NL", "IDEAL_CHECKOUT_COUNTRY"))
    checkout_currency = currency_for_country(checkout_country)
    last_error = ""
    stop_event = Event()

    ctx.log(
        "starting iDEAL extraction: "
        f"proxy_chain=JP/NL, checkout={checkout_country}/{checkout_currency}, "
        f"locale={_payment_browser_locale(ctx)}, checkout_retry={checkout_retry}, ideal_retry={ideal_retry}, workers={workers}."
    )
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Any, int] = {}
    try:
        for attempt in range(1, ideal_retry + 1):
            futures[
                executor.submit(
                    run_ideal_attempt,
                    ctx,
                    access_token,
                    session_token,
                    proxy_seeds,
                    attempt,
                    ideal_retry,
                    checkout_retry,
                    checkout_country,
                    checkout_currency,
                    stop_event,
                )
            ] = attempt

        for future in as_completed(futures):
            attempt = futures.get(future, 0)
            try:
                _attempt, redirect_url, error = future.result()
            except Exception as exc:
                redirect_url = ""
                error = str(exc)
                ctx.log(f"iDEAL extraction {attempt}/{ideal_retry} exception: {error[:300]}", "[WARN] ")
            if redirect_url:
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                ctx.log(f"iDEAL final payment URL: {redirect_url}")
                print("\n===== result =====")
                print(f"iDEAL final payment URL:\n{redirect_url}")
                return 0
            last_error = error or last_error
            if is_user_already_paid_error(error):
                ctx.log("detected User is already paid; task finished")
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                return 0
    finally:
        executor.shutdown(wait=True, cancel_futures=stop_event.is_set())

    ctx.log(f"all iDEAL attempts failed: {last_error}", "[ERROR] ")
    return 1


def run_ideal_single_link_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    ideal_workers = _cfg_int(ctx, "workers", 1, alias="IDEAL_WORKERS")
    if ideal_workers > 1:
        return run_ideal_single_link_parallel_mode(ctx, access_token, session_token, proxy_seeds)

    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="IDEAL_CHECKOUT_RETRY_MAX")
    ideal_retry = _cfg_int(ctx, "max_retry", 5, alias="IDEAL_MAX_RETRY")
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", "NL", "IDEAL_CHECKOUT_COUNTRY"))
    checkout_currency = currency_for_country(checkout_country)
    last_error = ""
    stop_event = Event()
    attempted_seed_keys: set[str] = set()

    ctx.log(
        "starting iDEAL extraction: "
        f"proxy_chain=JP/NL, checkout={checkout_country}/{checkout_currency}, "
        f"locale={_payment_browser_locale(ctx)}, checkout_retry={checkout_retry}, ideal_retry={ideal_retry}."
    )

    for attempt in range(1, ideal_retry + 1):
        available_seeds = [
            proxy_seed
            for proxy_seed in proxy_seeds
            if proxy_chain_key(proxy_seed) not in attempted_seed_keys
        ]
        if not available_seeds:
            last_error = last_error or "all proxy seeds attempted"
            ctx.log("all proxy seeds have been attempted for this iDEAL task", "[WARN] ")
            break
        checkout_candidates = _pick_seed_candidates(ctx, available_seeds, checkout_retry)
        for proxy_seed in checkout_candidates:
            attempted_seed_keys.add(proxy_chain_key(proxy_seed))

        attempt_no, redirect_url, error = run_ideal_attempt(
            ctx,
            access_token,
            session_token,
            checkout_candidates,
            attempt,
            ideal_retry,
            checkout_retry,
            checkout_country,
            checkout_currency,
            stop_event,
        )
        _ = attempt_no
        if redirect_url:
            ctx.log(f"iDEAL final payment URL: {redirect_url}")
            print("\n===== result =====")
            print(f"iDEAL final payment URL:\n{redirect_url}")
            return 0
        last_error = error or last_error
        if is_user_already_paid_error(error):
            ctx.log("detected User is already paid; task finished")
            return 0
        ctx.log(f"iDEAL extraction {attempt}/{ideal_retry} ended without final URL", "[WARN] ")
        time.sleep(0.5)

    ctx.log(f"all iDEAL attempts failed: {last_error}", "[ERROR] ")
    return 1
