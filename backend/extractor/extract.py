"""UPI extraction orchestration.

Main-flow port from the source project's ``upi/upi_extract.py``.  This layer
owns token loading, proxy seed routing, retry loops, and CLI entry points.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import unquote

from .checkout import (
    amount_from_payload,
    build_chatgpt_session,
    create_checkout,
    is_checkout_not_active_error,
    is_user_already_paid_error,
    random_user_agent,
    stripe_init,
    update_checkout_promotion,
    update_upi_checkout_taxes,
)
from .config import DEFAULT_STRIPE_PK
from .context import ExtractionContext
from .provider import (
    build_ctx,
    checkout_snapshot,
    collect_strings,
    first_value_by_key,
    infer_processor_entity,
    is_approve_failure_error,
    log_payment_page_summary,
    resolve_confirm_payload_upi,
    resolve_external_redirect,
    should_retry_second_confirm_after_approve,
    stripe_confirm_upi,
    stripe_create_upi_pm,
    stripe_update_customer_data,
    stripe_update_tax_region,
    upi_billing_profile,
)
from .proxy import (
    currency_for_country,
    is_upi_unavailable_error,
    normalize_country,
    parse_proxy_chain_seed,
    proxy_chain_key,
    proxy_for_country,
    proxy_key,
    proxy_label,
)
from .session import new_session


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
    return _cfg_str(ctx, "browser_locale", "en-IN", "UPI_BROWSER_LOCALE") or "en-IN"


def token_key_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def find_named_token(payload: Any, aliases: tuple[str, ...]) -> str:
    wanted = {token_key_name(item) for item in aliases}
    if isinstance(payload, dict):
        cookie_name = token_key_name(payload.get("name") or payload.get("key"))
        if cookie_name in wanted:
            for value_key in ("value", "token", "content"):
                value = str(payload.get(value_key) or "").strip()
                if value:
                    return value
        for key, value in payload.items():
            if token_key_name(key) in wanted and isinstance(value, (str, int, float)):
                found = str(value).strip()
                if found:
                    return found
        for value in payload.values():
            found = find_named_token(value, aliases)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_named_token(item, aliases)
            if found:
                return found
    return ""


def find_session_cookie(payload: Any) -> str:
    for value in collect_strings(payload):
        match = re.search(r"(?:^|[;\s])__Secure-next-auth\.session-token=([^;\s]+)", value)
        if match:
            return unquote(match.group(1))
    return ""


def normalize_token(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", ""
    session_token = ""
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            access_token = find_named_token(
                data,
                (
                    "accessToken",
                    "access_token",
                    "token",
                    "bearerToken",
                    "bearer_token",
                    "jwt",
                ),
            )
            session_token = find_named_token(
                data,
                (
                    "sessionToken",
                    "session_token",
                    "nextAuthSessionToken",
                    "next_auth_session_token",
                    "__Secure-next-auth.session-token",
                    "secureNextAuthSessionToken",
                ),
            ) or find_session_cookie(data)
            text = access_token
        except json.JSONDecodeError:
            pass
    return text, session_token


def load_token(ctx: ExtractionContext) -> tuple[str, str]:
    for key in ("PP_TOKEN", "UPI_TOKEN"):
        value = ctx.cfg_str(key, "").strip()
        if value:
            ctx.log(f"using config token {key}")
            token, session_token = normalize_token(value)
            env_session = ctx.cfg_str("PP_SESSION_TOKEN", "").strip()
            if env_session or session_token:
                ctx.log("loaded sessionToken cookie")
            return token, env_session or session_token

    for path in (ctx.script_dir / "token.txt",):
        if not path.exists():
            continue
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16", "utf-8", "ascii"):
            try:
                text = raw.decode(encoding).strip()
                break
            except UnicodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="ignore").strip()
        if text:
            ctx.log("using token file")
            token, session_token = normalize_token(text)
            env_session = ctx.cfg_str("PP_SESSION_TOKEN", "").strip()
            if env_session or session_token:
                ctx.log("loaded sessionToken cookie")
            return token, env_session or session_token

    if not sys.stdin.isatty():
        raise RuntimeError("access_token is required")

    token = input("请输入 access_token: ").strip()
    session_token = ctx.cfg_str("PP_SESSION_TOKEN", "").strip()
    token, parsed_session = normalize_token(token)
    return token, session_token or parsed_session


def upi_proxy_chain(ctx: ExtractionContext, proxy_seed: str) -> tuple[str, str, str]:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if explicit_chain:
        return (
            explicit_chain["checkout"],
            explicit_chain["promotion"],
            explicit_chain["provider"],
        )
    checkout_proxy = proxy_for_country(proxy_seed, ctx.bootstrap_country)
    promotion_proxy = proxy_for_country(proxy_seed, ctx.promotion_country)
    provider_proxy = proxy_for_country(proxy_seed, ctx.provider_country)
    return checkout_proxy, promotion_proxy, provider_proxy


def log_upi_proxy_chain(
    ctx: ExtractionContext,
    proxy_seed: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
) -> None:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if not _cfg_bool(ctx, "use_promotion_stage", False, "UPI_USE_PROMOTION_STAGE"):
        ctx.log(
            "proxy chain: "
            f"{ctx.bootstrap_country} checkout={proxy_label(checkout_proxy)}; "
            f"{ctx.provider_country} provider/approve={proxy_label(provider_proxy)}"
        )
        return
    if explicit_chain:
        promotion_chain = f"{ctx.promotion_country}={proxy_label(promotion_proxy)}"
    else:
        promotion_chain = " -> ".join(
            f"{country}={proxy_label(proxy_for_country(proxy_seed, country))}"
            for country in ctx.promotion_countries
        )
    ctx.log(
        "proxy chain: "
        f"{ctx.bootstrap_country} checkout={proxy_label(checkout_proxy)}; "
        f"{promotion_chain}; "
        f"{ctx.provider_country} provider/approve={proxy_label(provider_proxy)}"
    )


def run_provider_flow(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
    device_id: str,
    checkout: dict[str, str],
    billing: dict[str, str],
    stop_event: Event | None = None,
) -> tuple[str, list[str]]:
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", ctx.bootstrap_country, "UPI_CHECKOUT_COUNTRY"))
    stripe_pk = checkout.get("stripe_pk") or DEFAULT_STRIPE_PK

    def inspect_init(payload: dict[str, Any], stage: str) -> tuple[dict[str, Any], int]:
        current_ctx = build_ctx(ctx, payload, checkout)
        current_amount = int(current_ctx.get("checkout_amount") or 0)
        amount_major = current_amount / 100
        ctx.log(f"{stage} Stripe init succeeded, amount={checkout['currency']} {amount_major:.2f}")
        payment_method_types = first_value_by_key(payload, "payment_method_types")
        if isinstance(payment_method_types, list):
            methods = [str(item).lower() for item in payment_method_types]
            ctx.log(f"Stripe payment methods: {methods}")
            if "upi" not in methods:
                raise RuntimeError(
                    f"UPI_unavailable: {stage} amount={current_amount}; "
                    f"payment_method_types={methods}"
                )
        return current_ctx, current_amount

    hosted_url = ""
    stripe_ctx: dict[str, Any] = {}
    amount = 0

    if _cfg_bool(ctx, "use_promotion_stage", False, "UPI_USE_PROMOTION_STAGE"):
        ctx.log(
            f"{ctx.bootstrap_country} Bootstrap Stripe init "
            f"(PM={billing['country']}, proxy={proxy_label(checkout_proxy)})..."
        )
        init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, checkout_proxy)
        if not checkout.get("processor_entity"):
            processor_entity = infer_processor_entity(init_payload)
            if processor_entity:
                checkout["processor_entity"] = processor_entity
                ctx.log(f"inferred processor_entity={processor_entity} from Stripe init")
        inspect_init(init_payload, f"{ctx.bootstrap_country} Bootstrap")
        if stop_event and stop_event.is_set():
            raise RuntimeError("task stopped, skipping attempt")

        for promotion_index, promotion_country in enumerate(ctx.promotion_countries, start=1):
            current_promotion_proxy = proxy_for_country(promotion_proxy, promotion_country)
            stage_label = f"{promotion_country} checkout/update {promotion_index}/{len(ctx.promotion_countries)}"
            ctx.log(f"{stage_label}: proxy={proxy_label(current_promotion_proxy)}")
            try:
                promotion_chatgpt = build_chatgpt_session(
                    ctx, access_token, device_id, current_promotion_proxy, session_token
                )
                update_checkout_promotion(ctx, promotion_chatgpt, checkout, promotion_country)
            except Exception as exc:
                if is_checkout_not_active_error(exc):
                    raise
                raise RuntimeError(f"promotion stage failed: {exc}") from exc
            ctx.record_proxy_result("promotion", current_promotion_proxy, True, "promotion_update_success")

            ctx.log(
                f"{stage_label} then refresh Stripe through {ctx.provider_country}: "
                f"proxy={proxy_label(provider_proxy)}"
            )
            init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
            hosted_url = str(init_payload.get("stripe_hosted_url") or hosted_url or "")
            stripe_ctx, amount = inspect_init(
                init_payload, f"{promotion_country} updated then {ctx.provider_country}"
            )
            ctx.record_checkout_zero_result(checkout_proxy, checkout_country, amount)
            if amount == 0:
                ctx.log("promotion amount is 0; continuing UPI extraction")
                break
            if promotion_index < len(ctx.promotion_countries):
                ctx.log(f"{promotion_country} amount still non-zero; continuing next checkout/update", "[WARN] ")
                continue
            raise RuntimeError(f"zero promotion not active, amount minor={amount}; refusing non-zero UPI link")
    else:
        ctx.log(
            "reference UPI flow: skip checkout/update promotion stage; "
            f"Stripe init through {ctx.provider_country} provider={proxy_label(provider_proxy)}"
        )
        init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
        hosted_url = str(init_payload.get("stripe_hosted_url") or hosted_url or "")
        if not checkout.get("processor_entity"):
            processor_entity = infer_processor_entity(init_payload)
            if processor_entity:
                checkout["processor_entity"] = processor_entity
                ctx.log(f"inferred processor_entity={processor_entity} from Stripe init")
        stripe_ctx, amount = inspect_init(init_payload, f"{ctx.provider_country} provider")
        ctx.record_checkout_zero_result(checkout_proxy, checkout_country, amount)
        if _cfg_bool(ctx, "require_zero", True, "UPI_REQUIRE_ZERO") and amount != 0:
            raise RuntimeError(f"zero promotion not active, amount minor={amount}; refusing non-zero UPI link")
        ctx.log("amount is 0; continuing UPI extraction" if amount == 0 else f"non-zero amount accepted by config: minor={amount}")

    stripe = new_session(ctx, provider_proxy)
    stripe.headers.update({"User-Agent": random_user_agent(), "Accept-Language": "en-US,en;q=0.9"})

    if _cfg_bool(ctx, "update_tax_region", False, "UPI_UPDATE_TAX_REGION"):
        ctx.log(f"syncing {ctx.provider_country} checkout/taxes and Stripe tax_region...")
        tax_chatgpt = build_chatgpt_session(ctx, access_token, device_id, provider_proxy, session_token)
        update_upi_checkout_taxes(ctx, tax_chatgpt, checkout, billing)
        stripe_update_tax_region(ctx, stripe, checkout["cs_id"], stripe_pk, stripe_ctx, billing)
        init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
        hosted_url = str(init_payload.get("stripe_hosted_url") or hosted_url or "")
        stripe_ctx, amount = inspect_init(init_payload, f"{ctx.provider_country} tax sync")
        ctx.record_checkout_zero_result(checkout_proxy, checkout_country, amount)
        if amount != 0:
            raise RuntimeError(f"zero promotion not active, amount minor={amount}; refusing non-zero UPI link")

    pm_id = ""
    if _cfg_bool(ctx, "confirm_inline_pm", False, "UPI_CONFIRM_INLINE_PM"):
        ctx.log(
            f"UPI confirm inline billing: {billing['name']} / "
            f"{billing['line1']} / {billing['city']} {billing['postal_code']}"
        )
    else:
        ctx.log(f"creating PM (UPI): {billing['country']} {billing['name']} / {billing['city']}")
        pm_id = stripe_create_upi_pm(ctx, stripe, checkout["cs_id"], stripe_pk, billing, stripe_ctx)
        ctx.log(f"PM created: {pm_id}")

    if _cfg_bool(ctx, "update_customer_data", False, "UPI_UPDATE_CUSTOMER_DATA"):
        ctx.log(
            f"submitting customer data: {billing['name']} / {billing['line1']} / "
            f"{billing['city']} {billing['postal_code']} / {billing['email']}"
        )
        stripe_update_customer_data(ctx, stripe, checkout["cs_id"], stripe_pk, stripe_ctx, billing)

    if _cfg_bool(ctx, "checkout_snapshot", False, "UPI_CHECKOUT_SNAPSHOT"):
        snapshot_chatgpt = build_chatgpt_session(ctx, access_token, device_id, provider_proxy, session_token)
        checkout_snapshot(ctx, snapshot_chatgpt, checkout, billing)

    ctx.log("Stripe confirm (expected=UPI)...")
    confirm_payload = stripe_confirm_upi(
        ctx, stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, stripe_ctx, checkout, hosted_url, billing
    )
    ctx.log("Stripe confirm succeeded, parsing redirect...")
    log_payment_page_summary(ctx, "confirm", confirm_payload)
    if stop_event and stop_event.is_set():
        raise RuntimeError("task stopped, skipping attempt")

    approve_proxy = ""
    qr_urls: list[str] = []
    try:
        redirect_url, qr_urls, approve_proxy = resolve_confirm_payload_upi(
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
        ctx.log(f"no redirect after approve/confirm; refreshing init for second confirm: {str(exc)[:180]}", "[WARN] ")
        init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
        hosted_url = str(init_payload.get("stripe_hosted_url") or hosted_url or "")
        stripe_ctx = build_ctx(ctx, init_payload, checkout)
        confirm_payload = stripe_confirm_upi(
            ctx, stripe, checkout["cs_id"], pm_id, stripe_pk, init_payload, stripe_ctx, checkout, hosted_url, billing
        )
        ctx.log("second Stripe confirm succeeded, parsing redirect...")
        log_payment_page_summary(ctx, "second_confirm", confirm_payload)
        redirect_url, qr_urls, retry_approve_proxy = resolve_confirm_payload_upi(
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
        ctx.log("completed; remembered checkout/provider/approve combo")

    if redirect_url:
        final_url = resolve_external_redirect(ctx, stripe, redirect_url)
        if final_url and final_url != redirect_url:
            ctx.log(f"resolved final redirect: {final_url[:180]}")
            redirect_url = final_url

    return redirect_url, list(dict.fromkeys(qr_urls))


def run_once(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
    attempt: int,
    max_retry: int,
    stop_event: Event | None = None,
) -> tuple[str, list[str]]:
    if stop_event and stop_event.is_set():
        raise RuntimeError("task stopped, skipping attempt")
    device_id = str(uuid.uuid4())
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", ctx.bootstrap_country, "UPI_CHECKOUT_COUNTRY"))
    billing = upi_billing_profile(ctx)
    ctx.log(f"starting UPI extraction attempt {attempt}/{max_retry}")
    ctx.log(
        "combo: "
        f"{checkout_country} / {billing['country']} / {currency_for_country(checkout_country)} / "
        f"{_payment_browser_locale(ctx)} / {ctx.provider_country_label}"
    )

    try:
        proxy_seed = checkout_proxy
        checkout_proxy, promotion_proxy, provider_proxy = upi_proxy_chain(ctx, proxy_seed)
        log_upi_proxy_chain(ctx, proxy_seed, checkout_proxy, promotion_proxy, provider_proxy)
        ctx.log(f"round proxies: checkout={proxy_label(checkout_proxy)}; Stripe/UPI={proxy_label(provider_proxy)}")
        zero_status, zero_amount, _zero_checked_at = ctx.checkout_zero_cache_status(checkout_proxy, checkout_country)
        if zero_status == "ok":
            ctx.log(f"checkout zero cache hit: amount={zero_amount}")
        elif zero_status == "bad":
            ctx.log(f"checkout zero cache bad: last amount={zero_amount}; validating again", "[WARN] ")
        chatgpt = build_chatgpt_session(ctx, access_token, device_id, checkout_proxy, session_token)
        checkout = create_checkout(ctx, chatgpt, checkout_country)
    except Exception as exc:
        if is_user_already_paid_error(exc):
            raise RuntimeError("user already paid: User is already paid") from exc
        if is_checkout_not_active_error(exc):
            raise
        raise RuntimeError(f"checkout stage failed: {exc}") from exc
    return run_provider_flow(
        ctx,
        access_token,
        session_token,
        checkout_proxy,
        promotion_proxy,
        provider_proxy,
        [provider_proxy],
        device_id,
        checkout,
        billing,
        stop_event,
    )


def run_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    promotion_proxy: str,
    provider_proxy: str,
    approve_pool: list[str],
    attempt: int,
    max_retry: int,
    stop_event: Event | None = None,
    batch_no: int = 0,
    batch_total: int = 0,
) -> tuple[int, str, list[str], str, str, str]:
    previous_log_context = getattr(ctx.log_context, "prefix", "")
    if batch_no > 0:
        ctx.log_context.prefix = f"[batch {batch_no}/{batch_total or '?'}][attempt {attempt}/{max_retry}] "
    else:
        ctx.log_context.prefix = f"[attempt {attempt}/{max_retry}] "
    try:
        redirect_url, qr_urls = run_once(
            ctx,
            access_token,
            session_token,
            checkout_proxy,
            promotion_proxy,
            provider_proxy,
            approve_pool,
            attempt,
            max_retry,
            stop_event,
        )
        has_result = bool(redirect_url)
        if has_result and stop_event:
            stop_event.set()
        if has_result:
            ctx.record_proxy_pair_result(checkout_proxy, provider_proxy, True, "success")
        else:
            ctx.record_proxy_result("provider", provider_proxy, False, "no_redirect_url")
        return attempt, redirect_url, qr_urls, checkout_proxy, provider_proxy, ""
    except Exception as exc:
        error = str(exc)
        if error.startswith("task stopped"):
            return attempt, "", [], checkout_proxy, provider_proxy, ""
        if is_user_already_paid_error(error):
            ctx.log("detected User is already paid; stopping task")
            if stop_event:
                stop_event.set()
            return attempt, "", [], checkout_proxy, provider_proxy, error
        if is_upi_unavailable_error(error):
            ctx.log(f"attempt {attempt}/{max_retry} checkout did not offer UPI; keeping proxy and continuing", "[WARN] ")
            return attempt, "", [], checkout_proxy, provider_proxy, error
        if is_checkout_not_active_error(error):
            ctx.log(f"attempt {attempt}/{max_retry} session expired; skipping without proxy failure", "[WARN] ")
            return attempt, "", [], checkout_proxy, provider_proxy, error
        ctx.record_failure_by_stage(error, checkout_proxy, provider_proxy, promotion_proxy)
        ctx.log(f"attempt {attempt}/{max_retry} failed: {error[:300]}", "[WARN] ")
        return attempt, "", [], checkout_proxy, provider_proxy, error
    finally:
        ctx.log_context.prefix = previous_log_context


def successful_pair_preferences(
    ctx: ExtractionContext,
    checkout_proxies: list[str],
    provider_proxies: list[str],
) -> dict[str, list[str]]:
    if not _cfg_bool(ctx, "proxy_score", True, "UPI_PROXY_SCORE"):
        return {}
    checkout_by_key = {proxy_key(proxy): proxy for proxy in checkout_proxies}
    provider_by_key = {proxy_key(proxy): proxy for proxy in provider_proxies}
    pair_state = ctx.load_proxy_state().get("pair", {})
    if not isinstance(pair_state, dict):
        return {}

    candidates: list[tuple[int, int, str, str]] = []
    for record in pair_state.values():
        if not isinstance(record, dict):
            continue
        success_count = int(record.get("success") or 0)
        if success_count <= 0:
            continue
        checkout_proxy = checkout_by_key.get(str(record.get("checkout") or ""))
        provider_proxy = provider_by_key.get(str(record.get("provider") or ""))
        if checkout_proxy and provider_proxy:
            candidates.append((success_count, int(record.get("last_success") or 0), checkout_proxy, provider_proxy))

    candidates.sort(reverse=True)
    preferences: dict[str, list[str]] = {}
    for _success_count, _last_success, checkout_proxy, provider_proxy in candidates:
        providers = preferences.setdefault(checkout_proxy, [])
        if provider_proxy not in providers:
            providers.append(provider_proxy)
    return preferences


def build_attempt_batches(
    ctx: ExtractionContext,
    checkout_proxies: list[str],
    provider_proxies: list[str],
    max_attempts: int,
) -> list[tuple[str, list[str]]]:
    per_checkout = _cfg_int(ctx, "provider_per_checkout", 30, alias="UPI_PROVIDER_PER_CHECKOUT")
    provider_pool = provider_proxies[:]
    preferred_pairs = successful_pair_preferences(ctx, checkout_proxies, provider_proxies)
    reserved_provider_owner: dict[str, str] = {}
    for checkout_proxy, preferred_providers in preferred_pairs.items():
        for provider_proxy in preferred_providers:
            reserved_provider_owner.setdefault(provider_proxy, checkout_proxy)
    used_providers: set[str] = set()
    batches: list[tuple[str, list[str]]] = []
    provider_index = 0
    attempt_count = 0
    preferred_count = 0
    for checkout_proxy in checkout_proxies:
        batch: list[str] = []
        for provider_proxy in preferred_pairs.get(checkout_proxy, []):
            if len(batch) >= per_checkout or attempt_count >= max_attempts:
                break
            if provider_proxy in used_providers:
                continue
            batch.append(provider_proxy)
            used_providers.add(provider_proxy)
            attempt_count += 1
            preferred_count += 1
        while len(batch) < per_checkout and provider_index < len(provider_pool) and attempt_count < max_attempts:
            provider_proxy = provider_pool[provider_index]
            provider_index += 1
            if provider_proxy in used_providers:
                continue
            reserved_owner = reserved_provider_owner.get(provider_proxy)
            if reserved_owner and reserved_owner != checkout_proxy:
                continue
            batch.append(provider_proxy)
            used_providers.add(provider_proxy)
            attempt_count += 1
        if batch:
            batches.append((checkout_proxy, batch))
        if attempt_count >= max_attempts:
            break
    if preferred_count:
        ctx.log(f"scheduling preferred successful combos: {preferred_count}")
    return batches


def is_preferred_proxy(ctx: ExtractionContext, group: str, proxy: str) -> bool:
    if not group or not _cfg_bool(ctx, "proxy_score", True, "UPI_PROXY_SCORE"):
        return False
    state = ctx.load_proxy_state().get(group, {})
    if not isinstance(state, dict):
        return False
    record = state.get(proxy_key(proxy), {})
    if not isinstance(record, dict):
        return False
    return int(record.get("success") or 0) > 0


def pick_random_proxies(ctx: ExtractionContext, proxies: list[str], limit: int, group: str = "") -> list[str]:
    if group:
        proxies = ctx.order_proxy_group(group, proxies)
    preferred = [proxy for proxy in proxies if is_preferred_proxy(ctx, group, proxy)]
    preferred_set = set(preferred)
    rest = [proxy for proxy in proxies if proxy not in preferred_set]
    if limit >= len(proxies):
        random.shuffle(rest)
        return preferred + rest
    selected = preferred[:limit]
    remain_count = limit - len(selected)
    if remain_count > 0:
        selected.extend(random.sample(rest, min(remain_count, len(rest))))
    return selected


def run_single_link_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
    attempt: int,
    upi_retry: int,
    checkout_retry: int,
    checkout_country: str,
    checkout_currency: str,
    stop_event: Event,
) -> tuple[int, str, str, bool]:
    previous_log_context = getattr(ctx.log_context, "prefix", "")
    ctx.log_context.prefix = f"[UPI {attempt}/{upi_retry}] "
    last_error = ""
    approve_blocked = False
    checkout_proxy_used = ""
    try:
        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt", False
        billing = upi_billing_profile(ctx)
        pm_country = billing["country"]
        device_id = str(uuid.uuid4())
        checkout_candidates = pick_random_proxies(ctx, proxy_seeds, checkout_retry, "seed")
        checkout: dict[str, str] | None = None
        promotion_proxy = ""
        provider_proxy = ""

        ctx.log(f"starting extraction {attempt}/{upi_retry}")
        ctx.log(
            f"Step 1: create ChatGPT checkout, checkout billing={checkout_country}/{checkout_currency}, "
            f"sampling up to {checkout_retry} seed nodes"
        )
        ctx.log(f"first PM country: {pm_country}")

        for checkout_index, proxy_seed in enumerate(checkout_candidates, start=1):
            if stop_event.is_set():
                return attempt, "", "task stopped, skipping attempt", False
            ctx.log_context.prefix = f"[UPI {attempt}/{upi_retry}][PM={pm_country}] "
            checkout_proxy = ""
            try:
                checkout_proxy, promotion_proxy, provider_proxy = upi_proxy_chain(ctx, proxy_seed)
                log_upi_proxy_chain(ctx, proxy_seed, checkout_proxy, promotion_proxy, provider_proxy)
                ctx.log(f"Checkout {checkout_index}/{len(checkout_candidates)}: {checkout_country}/{checkout_currency}, proxy={proxy_label(checkout_proxy)}")
                zero_status, zero_amount, _zero_checked_at = ctx.checkout_zero_cache_status(checkout_proxy, checkout_country)
                if zero_status == "ok":
                    ctx.log(f"checkout zero cache hit: amount={zero_amount}")
                elif zero_status == "bad":
                    ctx.log(f"checkout zero cache bad: last amount={zero_amount}; validating again", "[WARN] ")
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
                    return attempt, "", error, False
                if not is_checkout_not_active_error(error):
                    ctx.record_failure_by_stage(f"checkout stage failed: {error}", checkout_proxy or proxy_seed, "")
                ctx.log(f"Checkout {checkout_index}/{len(checkout_candidates)} failed: {error[:220]}", "[WARN] ")

        ctx.log_context.prefix = f"[UPI {attempt}/{upi_retry}] "
        if not checkout or not checkout_proxy_used:
            ctx.log(f"extraction {attempt}/{upi_retry} checkout stage failed", "[WARN] ")
            return attempt, "", last_error or "checkout_failed", False

        stripe_pk = checkout.get("stripe_pk") or DEFAULT_STRIPE_PK
        ctx.log(f"Stripe PK: {stripe_pk[:18]}...")
        ctx.log(f"Step 2: first attempt PM={pm_country}...")

        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt", False
        ctx.log_context.prefix = f"[UPI {attempt}/{upi_retry}][PM={pm_country}] "
        try:
            redirect_url, _qr_urls = run_provider_flow(
                ctx,
                access_token,
                session_token,
                checkout_proxy_used,
                promotion_proxy,
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
                return attempt, redirect_url, "", False
            last_error = "no_redirect_url"
            ctx.record_proxy_result("provider", provider_proxy, False, last_error)
        except Exception as exc:
            error = str(exc)
            last_error = error
            if is_checkout_not_active_error(error):
                ctx.log("session expired; not switching provider for this checkout", "[WARN] ")
            elif is_upi_unavailable_error(error):
                ctx.log("current checkout did not offer UPI; trying next checkout combo", "[WARN] ")
                return attempt, "", error, False
            else:
                ctx.record_failure_by_stage(error, checkout_proxy_used, provider_proxy, promotion_proxy)
                ctx.log(f"Provider failed: {error[:220]}", "[WARN] ")
                if is_approve_failure_error(error) and "approve blocked" in error:
                    approve_blocked = True

        ctx.log_context.prefix = f"[UPI {attempt}/{upi_retry}] "
        ctx.log(f"extraction {attempt}/{upi_retry} ended without final URL", "[WARN] ")
        return attempt, "", last_error, approve_blocked
    finally:
        ctx.log_context.prefix = previous_log_context


def run_single_link_parallel_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="UPI_CHECKOUT_RETRY_MAX")
    upi_retry = _cfg_int(ctx, "max_retry", 5, alias="UPI_MAX_RETRY")
    requested_workers = _cfg_int(ctx, "workers", 1, alias="UPI_WORKERS")
    worker_limit = _cfg_int(ctx, "workers_max", requested_workers, alias="UPI_WORKERS_MAX")
    workers = min(max(1, requested_workers), max(1, worker_limit), upi_retry)
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", ctx.bootstrap_country, "UPI_CHECKOUT_COUNTRY"))
    checkout_currency = currency_for_country(checkout_country)
    configured_pm_country = normalize_country(_cfg_str(ctx, "billing_country", ctx.provider_country, "UPI_BILLING_COUNTRY"))
    max_blocked = _cfg_int(ctx, "max_approve_blocked", upi_retry, alias="UPI_MAX_APPROVE_BLOCKED")
    approve_blocked_count = 0
    last_error = ""
    stop_event = Event()

    if requested_workers > workers:
        ctx.log(f"UPI concurrency reduced from {requested_workers} to {workers}", "[WARN] ")
    ctx.log(
        "starting UPI extraction: "
        f"checkout={checkout_country}/{checkout_currency}, PM={configured_pm_country}, locale={_payment_browser_locale(ctx)}, "
        f"checkout_retry={checkout_retry}, upi_retry={upi_retry}, workers={workers}."
    )

    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Any, int] = {}
    try:
        for attempt in range(1, upi_retry + 1):
            futures[
                executor.submit(
                    run_single_link_attempt,
                    ctx,
                    access_token,
                    session_token,
                    proxy_seeds,
                    attempt,
                    upi_retry,
                    checkout_retry,
                    checkout_country,
                    checkout_currency,
                    stop_event,
                )
            ] = attempt

        for future in as_completed(futures):
            try:
                attempt, redirect_url, error, approve_blocked = future.result()
            except Exception as exc:
                attempt = futures.get(future, 0)
                redirect_url = ""
                error = str(exc)
                approve_blocked = False
                ctx.log(f"extraction {attempt}/{upi_retry} exception: {error[:300]}", "[WARN] ")
            if redirect_url:
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                ctx.log(f"UPI final payment URL: {redirect_url}")
                print("\n===== result =====")
                print(f"UPI final payment URL:\n{redirect_url}")
                return 0
            last_error = error or last_error
            if is_user_already_paid_error(error):
                ctx.log("detected User is already paid; task finished")
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                return 0
            if approve_blocked:
                approve_blocked_count += 1
                ctx.log(f"approve blocked count: {approve_blocked_count}/{max_blocked}", "[WARN] ")
            if approve_blocked_count >= max_blocked:
                ctx.log("approve blocked limit reached; stopping", "[WARN] ")
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                return 1
    finally:
        executor.shutdown(wait=True, cancel_futures=stop_event.is_set())

    ctx.log(f"all attempts failed: {last_error}", "[ERROR] ")
    return 1


def run_single_link_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    upi_workers = _cfg_int(ctx, "workers", 1, alias="UPI_WORKERS")
    if upi_workers > 1:
        return run_single_link_parallel_mode(ctx, access_token, session_token, proxy_seeds)

    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="UPI_CHECKOUT_RETRY_MAX")
    upi_retry = _cfg_int(ctx, "max_retry", 5, alias="UPI_MAX_RETRY")
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", ctx.bootstrap_country, "UPI_CHECKOUT_COUNTRY"))
    checkout_currency = currency_for_country(checkout_country)
    configured_pm_country = normalize_country(_cfg_str(ctx, "billing_country", ctx.provider_country, "UPI_BILLING_COUNTRY"))
    max_blocked = _cfg_int(ctx, "max_approve_blocked", upi_retry, alias="UPI_MAX_APPROVE_BLOCKED")
    approve_blocked_count = 0
    last_error = ""
    stop_event = Event()
    attempted_seed_keys: set[str] = set()

    ctx.log(
        "starting UPI extraction: "
        f"checkout={checkout_country}/{checkout_currency}, PM={configured_pm_country}, locale={_payment_browser_locale(ctx)}, "
        f"checkout_retry={checkout_retry}, upi_retry={upi_retry}."
    )

    for attempt in range(1, upi_retry + 1):
        billing = upi_billing_profile(ctx)
        pm_country = billing["country"]
        device_id = str(uuid.uuid4())
        available_seeds = [
            proxy_seed
            for proxy_seed in proxy_seeds
            if proxy_chain_key(proxy_seed) not in attempted_seed_keys
        ]
        checkout_candidates = pick_random_proxies(ctx, available_seeds, checkout_retry, "seed")
        if not checkout_candidates:
            last_error = last_error or "all proxy seeds attempted"
            ctx.log("all proxy seeds have been attempted for this task", "[WARN] ")
            break
        checkout: dict[str, str] | None = None
        checkout_proxy_used = ""
        promotion_proxy = ""
        provider_proxy = ""

        ctx.log(f"starting extraction {attempt}/{upi_retry}")
        ctx.log(
            f"Step 1: create ChatGPT checkout, checkout billing={checkout_country}/{checkout_currency}, "
            f"sampling up to {checkout_retry} seed nodes"
        )
        ctx.log(f"first PM country: {pm_country}")

        for checkout_index, proxy_seed in enumerate(checkout_candidates, start=1):
            previous_log_context = getattr(ctx.log_context, "prefix", "")
            ctx.log_context.prefix = f"  [PM={pm_country}] "
            checkout_proxy = ""
            promotion_proxy = ""
            provider_proxy = ""
            chain_key = proxy_chain_key(proxy_seed)
            attempted_seed_keys.add(chain_key)
            try:
                checkout_proxy, promotion_proxy, provider_proxy = upi_proxy_chain(ctx, proxy_seed)
                log_upi_proxy_chain(ctx, proxy_seed, checkout_proxy, promotion_proxy, provider_proxy)
                ctx.log(
                    f"Checkout {checkout_index}/{len(checkout_candidates)}: "
                    f"{checkout_country}/{checkout_currency}, proxy={proxy_label(checkout_proxy)}, "
                    f"attempted seeds={len(attempted_seed_keys)}"
                )
                zero_status, zero_amount, _zero_checked_at = ctx.checkout_zero_cache_status(checkout_proxy, checkout_country)
                if zero_status == "ok":
                    ctx.log(f"checkout zero cache hit: amount={zero_amount}")
                elif zero_status == "bad":
                    ctx.log(f"checkout zero cache bad: last amount={zero_amount}; validating again", "[WARN] ")
                chatgpt = build_chatgpt_session(ctx, access_token, device_id, checkout_proxy, session_token)
                checkout = create_checkout(ctx, chatgpt, checkout_country)
                checkout_proxy_used = checkout_proxy
                break
            except Exception as exc:
                error = str(exc)
                last_error = error
                if is_user_already_paid_error(error):
                    ctx.log("detected User is already paid; task finished")
                    return 0
                if not is_checkout_not_active_error(error):
                    ctx.record_failure_by_stage(f"checkout stage failed: {error}", checkout_proxy or proxy_seed, "")
                ctx.log(f"Checkout {checkout_index}/{len(checkout_candidates)} failed: {error[:220]}", "[WARN] ")
            finally:
                ctx.log_context.prefix = previous_log_context

        if not checkout or not checkout_proxy_used:
            ctx.log(f"extraction {attempt}/{upi_retry} checkout stage failed; trying next extraction", "[WARN] ")
            continue

        stripe_pk = checkout.get("stripe_pk") or DEFAULT_STRIPE_PK
        ctx.log(f"Stripe PK: {stripe_pk[:18]}...")
        ctx.log(f"Step 2: first attempt PM={pm_country}...")

        previous_log_context = getattr(ctx.log_context, "prefix", "")
        ctx.log_context.prefix = f"  [PM={pm_country}] "
        try:
            redirect_url, _qr_urls = run_provider_flow(
                ctx,
                access_token,
                session_token,
                checkout_proxy_used,
                promotion_proxy,
                provider_proxy,
                [provider_proxy],
                device_id,
                checkout,
                billing,
                stop_event,
            )
            if redirect_url:
                ctx.record_proxy_result("seed", checkout_proxy_used, True, "success")
                ctx.log(f"UPI final payment URL: {redirect_url}")
                print("\n===== result =====")
                print(f"UPI final payment URL:\n{redirect_url}")
                return 0
            last_error = "no_redirect_url"
            ctx.record_proxy_result("seed", provider_proxy, False, last_error)
        except Exception as exc:
            error = str(exc)
            last_error = error
            if is_checkout_not_active_error(error):
                ctx.log("session expired; not switching provider for this checkout", "[WARN] ")
            elif is_upi_unavailable_error(error):
                ctx.log("current checkout did not offer UPI; trying next extraction", "[WARN] ")
            else:
                ctx.record_failure_by_stage(error, checkout_proxy_used, provider_proxy, promotion_proxy)
                ctx.log(f"Provider failed: {error[:220]}", "[WARN] ")
                if is_approve_failure_error(error) and "approve blocked" in error:
                    approve_blocked_count += 1
                    ctx.log(f"approve blocked count: {approve_blocked_count}/{max_blocked}", "[WARN] ")
        finally:
            ctx.log_context.prefix = previous_log_context

        if approve_blocked_count >= max_blocked:
            ctx.log("approve blocked limit reached; stopping", "[WARN] ")
            return 1
        ctx.log(f"extraction {attempt}/{upi_retry} ended without final URL", "[WARN] ")

    ctx.log(f"all attempts failed: {last_error}", "[ERROR] ")
    return 1


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        return max(minimum, default)


def _configured_country(name: str, default: str) -> str:
    value = str(os.environ.get(name, default) or default).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise RuntimeError(f"{name} must be a two-letter country code")
    return value


def _configured_countries(name: str, default: str) -> list[str]:
    value = str(os.environ.get(name, default) or default)
    countries = [part.strip().upper() for part in value.split(",") if part.strip()]
    if not countries or any(not re.fullmatch(r"[A-Z]{2}", country) for country in countries):
        raise RuntimeError(f"{name} must be comma-separated two-letter country codes")
    return countries


def config_from_env(script_dir: str | Path | None = None) -> dict[str, Any]:
    base_dir = Path(script_dir) if script_dir is not None else Path.cwd()
    bootstrap_country = _configured_country(
        "UPI_BOOTSTRAP_COUNTRY",
        os.environ.get("UPI_CHECKOUT_PROXY_COUNTRY", "JP"),
    )
    promotion_countries = _configured_countries("UPI_PROMOTION_COUNTRY", "IN")
    provider_country = _configured_country("UPI_PROVIDER_COUNTRY", os.environ.get("UPI_BILLING_COUNTRY", "IN"))
    config: dict[str, Any] = {
        "script_dir": str(base_dir),
        "bootstrap_country": bootstrap_country,
        "promotion_countries": promotion_countries,
        "provider_country": provider_country,
        "provider_country_label": os.environ.get("UPI_PROVIDER_COUNTRY_LABEL", provider_country).strip() or provider_country,
        "log_dir": os.environ.get("UPI_LOG_DIR", "").strip(),
        "dump_dir": os.environ.get("UPI_DUMP_DIR", "").strip(),
        "max_retry": _env_int("UPI_MAX_RETRY", 5),
        "provider_per_checkout": _env_int("UPI_PROVIDER_PER_CHECKOUT", 1),
        "max_approve_blocked": _env_int("UPI_MAX_APPROVE_BLOCKED", 5),
        "workers": _env_int("UPI_WORKERS", 1),
        "workers_max": _env_int("UPI_WORKERS_MAX", _env_int("UPI_WORKERS", 1)),
        "approve_retry_max": _env_int("UPI_APPROVE_RETRY_MAX", 10),
        "approve_sticky": _env_bool("UPI_APPROVE_STICKY", True),
        "follow_redirect": _env_bool("UPI_FOLLOW_REDIRECT", True),
        "require_zero": _env_bool("UPI_REQUIRE_ZERO", True),
        "checkout_retry": _env_int("UPI_CHECKOUT_RETRY_MAX", 5),
        "provider_retry": _env_int("UPI_PROVIDER_RETRY_MAX", 3),
        "ideal_max_minor_amount": _env_int("IDEAL_MAX_MINOR_AMOUNT", 50, minimum=0),
        "dump": _env_bool("UPI_DUMP", False),
        "dump_limit": _env_int("UPI_DUMP_LIMIT", 6000, minimum=500),
        "proxy_score": _env_bool("UPI_PROXY_SCORE", True),
        "proxy_skip_failed": _env_bool("UPI_PROXY_SKIP_FAILED", True),
        "proxy_remove_failed": _env_bool("UPI_PROXY_REMOVE_FAILED", True),
        "proxy_fail_cooldown": _env_int("UPI_PROXY_FAIL_COOLDOWN", 180, minimum=0),
        "proxy_fail_skip_after": _env_int("UPI_PROXY_FAIL_SKIP_AFTER", 1),
        "proxy_remove_after_fails": _env_int("UPI_PROXY_REMOVE_AFTER_FAILS", 3),
        "zero_cache": _env_bool("UPI_ZERO_CACHE", True),
        "zero_cache_scheduling": _env_bool("UPI_ZERO_CACHE_SCHEDULING", False),
        "zero_cache_skip_bad": _env_bool("UPI_ZERO_CACHE_SKIP_BAD", True),
        "zero_cache_ttl": _env_int("UPI_ZERO_CACHE_TTL", 86400, minimum=0),
        "confirm_inline_pm": _env_bool("UPI_CONFIRM_INLINE_PM", False),
        "update_tax_region": _env_bool("UPI_UPDATE_TAX_REGION", False),
        "use_promotion_stage": _env_bool("UPI_USE_PROMOTION_STAGE", False),
        "pre_proxy": (
            os.environ.get("UPI_PRE_PROXY", "").strip()
            or os.environ.get("PP_PRE_PROXY", "").strip()
            or os.environ.get("PP_LOCAL_PROXY", "").strip()
        ),
        "default_proxy_scheme": os.environ.get("UPI_PROXY_DEFAULT_SCHEME", "http").strip() or "http",
        "proxy_seed_file": (
            os.environ.get("UPI_PROXY_SEED_FILE", "").strip()
            or os.environ.get("PP_PROXY_SEED_FILE", "").strip()
            or str(base_dir / "proxy_seeds.txt")
        ),
        "proxy_state_file": os.environ.get("UPI_PROXY_STATE_FILE", "").strip() or str(base_dir / "proxy_state.json"),
        "stripe_pk": os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip(),
        "stripe_runtime_version": os.environ.get("PP_RUNTIME_VERSION", "").strip(),
        "browser_locale": os.environ.get("UPI_BROWSER_LOCALE", "en-IN").strip() or "en-IN",
        "elements_locale": os.environ.get("UPI_ELEMENTS_LOCALE", "en").strip() or "en",
        "browser_timezone": os.environ.get("UPI_BROWSER_TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata",
        "saved_payment_value": os.environ.get("UPI_SAVED_PAYMENT_VALUE", "never").strip() or "never",
        "billing_country": os.environ.get("UPI_BILLING_COUNTRY", provider_country).strip() or provider_country,
        "billing_email": os.environ.get("UPI_EMAIL", "redacted@example.invalid").strip() or "redacted@example.invalid",
        "billing_name": os.environ.get("UPI_NAME", "Aisha Sharma").strip() or "Aisha Sharma",
        "billing_line1": os.environ.get("UPI_LINE1", "24 Park Street").strip() or "24 Park Street",
        "billing_line2": os.environ.get("UPI_LINE2", "").strip(),
        "billing_city": os.environ.get("UPI_CITY", "Kolkata").strip() or "Kolkata",
        "billing_postal_code": os.environ.get("UPI_POSTAL_CODE", "700016").strip() or "700016",
        "billing_state": os.environ.get("UPI_STATE", "WB").strip() or "WB",
        "checkout_country": os.environ.get("UPI_CHECKOUT_COUNTRY", "IN").strip() or "IN",
    }
    for key in (
        "PP_TOKEN",
        "UPI_TOKEN",
        "PP_SESSION_TOKEN",
        "PP_PROMO_MODE",
        "PP_PROMO_ID",
        "PP_ENTRY_POINT",
        "PP_TRIAL_DAYS",
        "PP_EXPECTED_AMOUNT",
        "UPI_COUPON_FALLBACK_PROMO_CAMPAIGN",
        "UPI_DUMP_WARMUP",
        "UPI_APPROVE_WARMUP",
        "UPI_APPROVE_PARALLEL",
        "UPI_UPDATE_CUSTOMER_DATA",
        "UPI_CHECKOUT_SNAPSHOT",
        "UPI_USE_FIXED_BILLING",
        "UPI_USE_PROMOTION_STAGE",
        "IDEAL_MAX_MINOR_AMOUNT",
        "IDEAL_CHECKOUT_COUNTRY",
        "IDEAL_CHECKOUT_PROXY_COUNTRY",
        "IDEAL_PROVIDER_PROXY_COUNTRY",
        "IDEAL_BROWSER_LOCALE",
        "IDEAL_ELEMENTS_LOCALE",
        "IDEAL_BROWSER_TIMEZONE",
        "IDEAL_CHECKOUT_RETRY_MAX",
        "IDEAL_MAX_RETRY",
        "IDEAL_WORKERS",
        "IDEAL_WORKERS_MAX",
        "IDEAL_UPDATE_CUSTOMER_DATA",
        "IDEAL_UPDATE_TAX_REGION",
        "IDEAL_USE_FIXED_BILLING",
        "IDEAL_EMAIL",
        "IDEAL_NAME",
        "IDEAL_LINE1",
        "IDEAL_LINE2",
        "IDEAL_CITY",
        "IDEAL_POSTAL_CODE",
        "IDEAL_STATE",
        "IDEAL_BILLING_COUNTRY",
    ):
        if key in os.environ:
            config[key] = os.environ[key]
    return config


def main(ctx: ExtractionContext | None = None) -> int:
    ctx = ctx or ExtractionContext(config=config_from_env(Path.cwd()))
    access_token, session_token = load_token(ctx)
    if not access_token:
        ctx.log("access_token is empty", "[ERROR] ")
        return 1

    proxy_seeds = ctx.load_proxy_seeds()
    flow_mode = _cfg_str(ctx, "flow_mode", "single", "UPI_FLOW_MODE").strip().lower() or "single"
    if flow_mode != "single":
        ctx.log(f"UPI_FLOW_MODE={flow_mode} has been normalized to strict single seed flow", "[WARN] ")
    return run_single_link_mode(ctx, access_token, session_token, proxy_seeds)


if __name__ == "__main__":
    sys.exit(main())
