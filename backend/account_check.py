from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

from .extractor.context import ExtractionContext
from .extractor.checkout import (
    amount_from_payload,
    checkout_response_has_promo,
    checkout_response_has_trial,
)
from .extractor.proxy import currency_for_country, normalize_country
from .extractor.session import (
    CHATGPT_CLIENT_BUILD_NUMBER,
    CHATGPT_CLIENT_VERSION,
    DEFAULT_USER_AGENT,
    new_session,
)


CHATGPT_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
PLUS_PLAN_MARKERS = {
    "plus",
    "pro",
    "team",
    "business",
    "enterprise",
    "paid",
    "chatgptplus",
    "chatgptpro",
    "chatgptteam",
    "chatgptplusplan",
}


@dataclass
class TokenIdentity:
    token: str
    payload: dict[str, Any]
    token_ok: bool
    email: str | None = None
    account_id: str | None = None
    plan_type: str | None = None
    phone_number: str | None = None
    phone_verified: bool | None = None
    reg_type: str | None = None
    jwt_expired: bool = False
    jwt_exp_ms: int | None = None
    jwt_exp_in_sec: int | None = None


def _session_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_access_token(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""

    data = _session_json(text)
    if not data:
        return text

    candidates: list[Any] = [
        data.get("accessToken"),
        data.get("access_token"),
        data.get("token"),
    ]
    for key in ("extra", "session"):
        nested = data.get(key)
        if isinstance(nested, dict):
            candidates.extend([nested.get("accessToken"), nested.get("access_token"), nested.get("token")])

    for candidate in candidates:
        token = str(candidate or "").strip()
        if token:
            return token
    return ""


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        raw = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _dict_value(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _session_plan(session: dict[str, Any]) -> str | None:
    account = _dict_value(session, "account")
    for value in (session.get("plan_type"), session.get("planType"), account.get("plan_type"), account.get("planType")):
        text = _clean_str(value)
        if text:
            return text
    return None


def _parse_token_identity(raw: str) -> TokenIdentity:
    token = _extract_access_token(raw)
    payload = _decode_jwt_payload(token)
    session = _session_json(raw)
    auth = _dict_value(payload, "https://api.openai.com/auth")
    profile = _dict_value(payload, "https://api.openai.com/profile")
    user = _dict_value(session, "user")

    now = int(time.time())
    exp_raw = payload.get("exp")
    exp = int(exp_raw) if isinstance(exp_raw, (int, float, str)) and str(exp_raw).isdigit() else None
    jwt_exp_in_sec = exp - now if exp is not None else None
    jwt_expired = bool(exp is not None and exp <= now)

    phone_number = (
        _clean_str(auth.get("phone_number"))
        or _clean_str(profile.get("phone_number"))
        or _clean_str(session.get("phone_number"))
        or _clean_str(user.get("phone_number"))
    )
    phone_verified_raw = (
        auth.get("phone_verified")
        if "phone_verified" in auth
        else profile.get("phone_verified")
        if "phone_verified" in profile
        else session.get("phone_verified")
        if "phone_verified" in session
        else user.get("phone_verified")
    )

    return TokenIdentity(
        token=token,
        payload=payload,
        token_ok=bool(token and payload),
        email=(
            _clean_str(profile.get("email"))
            or _clean_str(user.get("email"))
            or _clean_str(session.get("email"))
        ),
        account_id=(
            _clean_str(auth.get("chatgpt_account_id"))
            or _clean_str(auth.get("account_id"))
            or _clean_str(session.get("account_id"))
            or _clean_str(session.get("accountId"))
            or _clean_str(payload.get("sub"))
        ),
        plan_type=(
            _clean_str(auth.get("chatgpt_plan_type"))
            or _session_plan(session)
            or _clean_str(payload.get("plan_type"))
        ),
        phone_number=phone_number,
        phone_verified=phone_verified_raw if isinstance(phone_verified_raw, bool) else None,
        reg_type=_clean_str(auth.get("reg_type")) or ("phone" if phone_number else "email"),
        jwt_expired=jwt_expired,
        jwt_exp_ms=exp * 1000 if exp is not None else None,
        jwt_exp_in_sec=jwt_exp_in_sec,
    )


def _plan_is_paid(plan_type: str | None) -> bool:
    plan = str(plan_type or "").strip().lower().replace(" ", "")
    return bool(plan and plan in PLUS_PLAN_MARKERS)


def _plus_signal(parsed: Any, text: str) -> bool:
    if isinstance(parsed, dict):
        code = str(parsed.get("code") or parsed.get("error") or parsed.get("message") or "").lower()
        if any(marker in code for marker in ("already_subscribed", "already subscribed", "active subscription", "user is already paid")):
            return True
        for key in ("plan_type", "planType", "accountPlan", "subscriptionPlan", "tier"):
            if str(parsed.get(key) or "").strip().lower().replace(" ", "") in PLUS_PLAN_MARKERS:
                return True
        account = parsed.get("account")
        if isinstance(account, dict) and any(
            str(account.get(key) or "").strip().lower().replace(" ", "") in PLUS_PLAN_MARKERS
            for key in ("plan_type", "planType", "accountPlan", "subscriptionPlan", "tier")
        ):
            return True
        if any(parsed.get(key) is True for key in ("isPaid", "is_paid", "hasPaidSubscription", "isPlus", "is_plus", "subscribed")):
            return True
    compact = "".join(str(text or "").lower().split())
    return any(
        marker in compact
        for marker in (
            '"plantype":"plus"',
            '"plantype":"pro"',
            '"plantype":"team"',
            '"plan_type":"plus"',
            '"plan_type":"pro"',
            '"plan_type":"team"',
            '"ispaid":true',
            '"is_plus":true',
            '"haspaidsubscription":true',
            "already_subscribed",
            "active_subscription",
            "userisalreadypaid",
        )
    )


def _checkout_trial_eligible(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "eligible"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or str(default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)) or str(default))
    except ValueError:
        value = default
    return max(minimum, value)


def _checkout_context() -> ExtractionContext:
    config = {
        "pre_proxy": (
            os.environ.get("ACCOUNT_CHECK_PRE_PROXY")
            or os.environ.get("UPI_PRE_PROXY")
            or os.environ.get("PP_PRE_PROXY")
            or os.environ.get("PP_LOCAL_PROXY")
            or ""
        ),
        "dump": os.environ.get("ACCOUNT_CHECK_DUMP", "").strip().lower() in {"1", "true", "yes", "on"},
    }
    return ExtractionContext(config=config)


def _checkout_session(ctx: ExtractionContext, identity: TokenIdentity) -> requests.Session:
    device_id = str(uuid.uuid4())
    session = new_session(ctx, os.environ.get("ACCOUNT_CHECK_PROXY", "").strip())
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": os.environ.get("ACCOUNT_CHECK_ACCEPT_LANGUAGE", "ko-KR,ko;q=0.9,en;q=0.8"),
        "Authorization": f"Bearer {identity.token}",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
        "oai-language": os.environ.get("ACCOUNT_CHECK_LANGUAGE", "ko-KR"),
        "oai-session-id": device_id,
        "oai-client-version": CHATGPT_CLIENT_VERSION,
        "oai-client-build-number": CHATGPT_CLIENT_BUILD_NUMBER,
        "x-openai-target-path": "/backend-api/payments/checkout",
        "x-openai-target-route": "/backend-api/payments/checkout",
    }
    if identity.account_id:
        headers["Chatgpt-Account-Id"] = identity.account_id
    session.headers.update(headers)
    return session


def _run_checkout_probe(identity: TokenIdentity, promo_id: str) -> dict[str, Any]:
    country = normalize_country(os.environ.get("ACCOUNT_CHECK_COUNTRY", "KR") or "KR")
    currency = os.environ.get("ACCOUNT_CHECK_CURRENCY", "").strip().upper() or currency_for_country(country)
    body = {
        "entry_point": os.environ.get("ACCOUNT_CHECK_ENTRY_POINT", "all_plans_pricing_modal"),
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "custom",
        "promo_campaign": {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": False,
        },
    }
    attempts = _env_int("ACCOUNT_CHECK_MAX_ATTEMPTS", 2, 1, 5)
    timeout = _env_float("ACCOUNT_CHECK_TIMEOUT", 30, 1)
    try:
        ctx = _checkout_context()
        session = _checkout_session(ctx, identity)
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "eligible": False,
            "reason": "checkout_probe_failed",
            "coupon_state": "unknown",
            "error": str(exc),
        }
    last_error = ""

    for attempt in range(1, attempts + 1):
        try:
            response = session.post(CHATGPT_CHECKOUT_URL, json=body, timeout=timeout)
        except Exception as exc:
            last_error = str(exc)
            continue

        try:
            payload = response.json()
        except ValueError:
            payload = None
        text = response.text[:5000]
        plus = response.status_code in {200, 400, 402, 409} and _plus_signal(payload, text)
        trial_eligible = (
            isinstance(payload, dict)
            and (_checkout_trial_eligible(payload.get("one_click_trial_eligible")) or checkout_response_has_trial(payload))
        )
        promo_visible = isinstance(payload, dict) and checkout_response_has_promo(payload)
        amount = amount_from_payload(payload) if isinstance(payload, dict) else 0

        if plus:
            return {
                "ok": True,
                "status": response.status_code,
                "eligible": False,
                "reason": "already_subscribed",
                "coupon_state": "already_used",
                "plus": True,
                "amount": amount,
                "promo_visible": promo_visible,
            }
        if response.status_code == 200:
            return {
                "ok": True,
                "status": response.status_code,
                "eligible": bool(trial_eligible),
                "reason": None if trial_eligible else "not_eligible",
                "coupon_state": "eligible" if trial_eligible else "not_eligible",
                "plus": False,
                "amount": amount,
                "promo_visible": promo_visible,
            }
        last_error = f"checkout probe failed HTTP {response.status_code}: {text[:500]}"
        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "status": response.status_code,
                "eligible": False,
                "reason": "token_rejected",
                "coupon_state": "unknown",
                "error": last_error,
            }

    return {
        "ok": False,
        "status": 0,
        "eligible": False,
        "reason": "checkout_probe_failed",
        "coupon_state": "unknown",
        "error": last_error or "checkout probe failed",
    }


def _base_response(identity: TokenIdentity, promo_id: str) -> dict[str, Any]:
    return {
        "token_ok": identity.token_ok,
        "eligible": False,
        "reason": None,
        "coupon_state": None,
        "promo_id": promo_id,
        "status": None,
        "email": identity.email,
        "account_id": identity.account_id,
        "plan_type": identity.plan_type,
        "phone_number": identity.phone_number,
        "phone_verified": identity.phone_verified,
        "reg_type": identity.reg_type,
        "jwt_expired": identity.jwt_expired,
        "jwt_exp_ms": identity.jwt_exp_ms,
        "jwt_exp_in_sec": identity.jwt_exp_in_sec,
        "upi_eligible": None,
        "upi_eligible_reason": None,
        "gcash_eligible": None,
        "gcash_eligible_reason": None,
        "ideal_eligible": None,
        "ideal_eligible_reason": None,
        "error": None,
    }


def _apply_channel_fields(result: dict[str, Any], eligible: bool | None, reason: str | None) -> None:
    result["upi_eligible"] = eligible
    result["gcash_eligible"] = eligible
    result["ideal_eligible"] = eligible
    result["upi_eligible_reason"] = None if eligible else reason
    result["gcash_eligible_reason"] = None if eligible else reason
    result["ideal_eligible_reason"] = None if eligible else reason


def check_account_eligibility(token: str, promo_id: str = "plus-1-month-free") -> dict[str, Any]:
    promo_id = str(promo_id or "plus-1-month-free").strip() or "plus-1-month-free"
    identity = _parse_token_identity(token)
    result = _base_response(identity, promo_id)

    if not identity.token_ok:
        result.update(
            {
                "reason": "invalid_token",
                "coupon_state": "unknown",
                "error": "Access Token is not a valid JWT, or Session JSON does not contain accessToken.",
            }
        )
        _apply_channel_fields(result, False, "invalid_token")
        return result

    if identity.jwt_expired:
        result.update({"reason": "jwt_expired", "coupon_state": "unknown", "error": "Access Token expired."})
        _apply_channel_fields(result, False, "jwt_expired")
        return result

    if _plan_is_paid(identity.plan_type):
        result.update({"reason": "already_subscribed", "coupon_state": "already_used"})
        _apply_channel_fields(result, False, "already_subscribed")
        return result

    probe = _run_checkout_probe(identity, promo_id)
    eligible = bool(probe.get("eligible"))
    reason = _clean_str(probe.get("reason"))
    result.update(
        {
            "eligible": eligible,
            "reason": reason,
            "coupon_state": _clean_str(probe.get("coupon_state")),
            "status": probe.get("status") if isinstance(probe.get("status"), int) else None,
            "error": _clean_str(probe.get("error")),
        }
    )
    _apply_channel_fields(result, eligible, reason)
    return result
