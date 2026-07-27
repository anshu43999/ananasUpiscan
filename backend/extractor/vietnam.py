"""Vietnam MoMo extraction orchestration.

The VN flow intentionally stops at Stripe init. MoMo availability is exposed by
Stripe's structured ``payment_method_types`` data, so there is no page scraping
or checkout-page payment method management here.
"""

from __future__ import annotations

import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Any

from .checkout import (
    amount_from_payload,
    build_chatgpt_session,
    checkout_page_url,
    create_checkout,
    is_checkout_not_active_error,
    is_user_already_paid_error,
    stripe_init,
)
from .config import DEFAULT_STRIPE_PK
from .context import ExtractionContext
from .provider import first_value_by_key, infer_processor_entity, log_payment_page_summary
from .proxy import (
    currency_for_country,
    normalize_country,
    parse_proxy_chain_seed,
    proxy_chain_key,
    proxy_for_country,
    proxy_label,
)


IGNORED_VN_METHODS = {"card", "paypal", "link"}


def _cfg_int(ctx: ExtractionContext, primary: str, default: int, minimum: int = 1, alias: str = "") -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: ExtractionContext, primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def _payment_methods_from_init(payload: Any) -> list[str]:
    raw = first_value_by_key(payload, "payment_method_types")
    if not isinstance(raw, list):
        return []
    return [str(item).strip().lower() for item in raw if str(item).strip()]


def _local_methods(methods: list[str]) -> list[str]:
    return [method for method in methods if method not in IGNORED_VN_METHODS]


def momo_proxy_chain(ctx: ExtractionContext, proxy_seed: str) -> tuple[str, str]:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if explicit_chain:
        return explicit_chain["checkout"], explicit_chain["provider"]
    checkout_proxy = proxy_for_country(proxy_seed, _cfg_str(ctx, "momo_checkout_proxy_country", "VN", "MOMO_CHECKOUT_PROXY_COUNTRY"))
    provider_proxy = proxy_for_country(proxy_seed, _cfg_str(ctx, "momo_provider_proxy_country", "VN", "MOMO_PROVIDER_PROXY_COUNTRY"))
    return checkout_proxy, provider_proxy


def log_momo_proxy_chain(ctx: ExtractionContext, proxy_seed: str, checkout_proxy: str, provider_proxy: str) -> None:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    prefix = "explicit" if explicit_chain else "derived"
    ctx.log(
        f"MoMo proxy chain ({prefix}): "
        f"VN checkout={proxy_label(checkout_proxy)}; VN Stripe init={proxy_label(provider_proxy)}"
    )


def _pick_seed_candidates(ctx: ExtractionContext, proxy_seeds: list[str], limit: int) -> list[str]:
    ordered = ctx.order_proxy_group("seed", proxy_seeds)
    if limit >= len(ordered):
        return ordered
    return random.sample(ordered, min(limit, len(ordered)))


def run_momo_probe(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    checkout_proxy: str,
    provider_proxy: str,
    device_id: str,
    checkout_country: str,
) -> str:
    chatgpt = build_chatgpt_session(ctx, access_token, device_id, checkout_proxy, session_token)
    checkout = create_checkout(ctx, chatgpt, checkout_country)
    stripe_pk = checkout.get("stripe_pk") or DEFAULT_STRIPE_PK

    ctx.log(f"Stripe init for VN/MoMo: proxy={proxy_label(provider_proxy)}")
    init_payload = stripe_init(ctx, checkout["cs_id"], stripe_pk, provider_proxy)
    log_payment_page_summary(ctx, "momo_init", init_payload)

    if not checkout.get("processor_entity"):
        processor_entity = infer_processor_entity(init_payload)
        if processor_entity:
            checkout["processor_entity"] = processor_entity
            ctx.log(f"inferred processor_entity={processor_entity} from Stripe init")

    methods = _payment_methods_from_init(init_payload)
    local_methods = _local_methods(methods)
    ctx.log(f"Stripe payment methods: {methods}")
    ctx.log(f"VN local payment methods after filtering card/paypal/link: {local_methods}")
    if "momo" not in local_methods:
        raise RuntimeError(f"MoMo_unavailable: payment_method_types={methods}")

    amount = amount_from_payload(init_payload)
    ctx.log(f"MoMo available: checkout={checkout_country}/{currency_for_country(checkout_country)}, amount_minor={amount}")
    url = str(init_payload.get("stripe_hosted_url") or "")
    if not url:
        url = checkout_page_url(checkout)
    return url


def run_momo_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
    attempt: int,
    momo_retry: int,
    checkout_retry: int,
    checkout_country: str,
    stop_event: Event,
) -> tuple[int, str, str]:
    previous_log_context = getattr(ctx.log_context, "prefix", "")
    ctx.log_context.prefix = f"[MoMo {attempt}/{momo_retry}] "
    last_error = ""
    checkout_proxy_used = ""
    provider_proxy = ""
    try:
        if stop_event.is_set():
            return attempt, "", "task stopped, skipping attempt"
        candidates = _pick_seed_candidates(ctx, proxy_seeds, checkout_retry)
        ctx.log(
            f"starting MoMo extraction {attempt}/{momo_retry}: "
            f"checkout={checkout_country}/{currency_for_country(checkout_country)}"
        )
        for checkout_index, proxy_seed in enumerate(candidates, start=1):
            if stop_event.is_set():
                return attempt, "", "task stopped, skipping attempt"
            try:
                checkout_proxy, provider_proxy = momo_proxy_chain(ctx, proxy_seed)
                checkout_proxy_used = checkout_proxy
                log_momo_proxy_chain(ctx, proxy_seed, checkout_proxy, provider_proxy)
                ctx.log(f"Checkout {checkout_index}/{len(candidates)}: proxy={proxy_label(checkout_proxy)}")
                result_url = run_momo_probe(
                    ctx,
                    access_token,
                    session_token,
                    checkout_proxy,
                    provider_proxy,
                    str(uuid.uuid4()),
                    checkout_country,
                )
                ctx.record_proxy_pair_result(checkout_proxy_used, provider_proxy, True, "momo_success")
                stop_event.set()
                return attempt, result_url, ""
            except Exception as exc:
                error = str(exc)
                last_error = error
                if is_user_already_paid_error(error):
                    ctx.log("detected User is already paid; stopping task")
                    stop_event.set()
                    return attempt, "", error
                if not is_checkout_not_active_error(error):
                    ctx.record_failure_by_stage(error, checkout_proxy_used, provider_proxy)
                ctx.log(f"MoMo checkout/init failed: {error[:220]}", "[WARN] ")
        return attempt, "", last_error or "momo_failed"
    finally:
        ctx.log_context.prefix = previous_log_context


def run_momo_single_link_parallel_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="MOMO_CHECKOUT_RETRY_MAX")
    momo_retry = _cfg_int(ctx, "max_retry", 5, alias="MOMO_MAX_RETRY")
    requested_workers = _cfg_int(ctx, "workers", 1, alias="MOMO_WORKERS")
    worker_limit = _cfg_int(ctx, "workers_max", requested_workers, alias="MOMO_WORKERS_MAX")
    workers = min(max(1, requested_workers), max(1, worker_limit), momo_retry)
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", "VN", "MOMO_CHECKOUT_COUNTRY"))
    stop_event = Event()
    last_error = ""

    ctx.log(
        "starting MoMo extraction: "
        f"proxy_chain=VN/VN, checkout={checkout_country}/{currency_for_country(checkout_country)}, "
        f"checkout_retry={checkout_retry}, momo_retry={momo_retry}, workers={workers}."
    )
    executor = ThreadPoolExecutor(max_workers=workers)
    futures: dict[Any, int] = {}
    try:
        for attempt in range(1, momo_retry + 1):
            futures[
                executor.submit(
                    run_momo_attempt,
                    ctx,
                    access_token,
                    session_token,
                    proxy_seeds,
                    attempt,
                    momo_retry,
                    checkout_retry,
                    checkout_country,
                    stop_event,
                )
            ] = attempt

        for future in as_completed(futures):
            attempt = futures.get(future, 0)
            try:
                _attempt, result_url, error = future.result()
            except Exception as exc:
                result_url = ""
                error = str(exc)
                ctx.log(f"MoMo extraction {attempt}/{momo_retry} exception: {error[:300]}", "[WARN] ")
            if result_url:
                stop_event.set()
                for pending in futures:
                    pending.cancel()
                ctx.log(f"MoMo final payment URL: {result_url}")
                print("\n===== result =====")
                print(f"MoMo final payment URL:\n{result_url}")
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

    ctx.log(f"all MoMo attempts failed: {last_error}", "[ERROR] ")
    return 1


def run_momo_single_link_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    momo_workers = _cfg_int(ctx, "workers", 1, alias="MOMO_WORKERS")
    if momo_workers > 1:
        return run_momo_single_link_parallel_mode(ctx, access_token, session_token, proxy_seeds)

    checkout_retry = _cfg_int(ctx, "checkout_retry", 5, alias="MOMO_CHECKOUT_RETRY_MAX")
    momo_retry = _cfg_int(ctx, "max_retry", 5, alias="MOMO_MAX_RETRY")
    checkout_country = normalize_country(_cfg_str(ctx, "checkout_country", "VN", "MOMO_CHECKOUT_COUNTRY"))
    stop_event = Event()
    attempted_seed_keys: set[str] = set()
    last_error = ""

    ctx.log(
        "starting MoMo extraction: "
        f"proxy_chain=VN/VN, checkout={checkout_country}/{currency_for_country(checkout_country)}, "
        f"checkout_retry={checkout_retry}, momo_retry={momo_retry}."
    )
    for attempt in range(1, momo_retry + 1):
        available_seeds = [
            proxy_seed
            for proxy_seed in proxy_seeds
            if proxy_chain_key(proxy_seed) not in attempted_seed_keys
        ]
        if not available_seeds:
            last_error = last_error or "all proxy seeds attempted"
            ctx.log("all proxy seeds have been attempted for this MoMo task", "[WARN] ")
            break
        candidates = _pick_seed_candidates(ctx, available_seeds, checkout_retry)
        for proxy_seed in candidates:
            attempted_seed_keys.add(proxy_chain_key(proxy_seed))

        _attempt, result_url, error = run_momo_attempt(
            ctx,
            access_token,
            session_token,
            candidates,
            attempt,
            momo_retry,
            checkout_retry,
            checkout_country,
            stop_event,
        )
        if result_url:
            ctx.log(f"MoMo final payment URL: {result_url}")
            print("\n===== result =====")
            print(f"MoMo final payment URL:\n{result_url}")
            return 0
        last_error = error or last_error
        if is_user_already_paid_error(error):
            ctx.log("detected User is already paid; task finished")
            return 0
        ctx.log(f"MoMo extraction {attempt}/{momo_retry} ended without final URL", "[WARN] ")
        time.sleep(0.5)

    ctx.log(f"all MoMo attempts failed: {last_error}", "[ERROR] ")
    return 1
