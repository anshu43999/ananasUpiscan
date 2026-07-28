"""Direct card checkout short-link extraction.

This channel follows the standalone checkout short-link extractor supplied by
the user: create a ChatGPT checkout with PH/PHP billing, apply the free-month
promotion through checkout/update, verify the amount is zero when available,
and return the checkout URL for card payment.
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape as html_unescape
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from .context import ExtractionContext
from .proxy import normalize_country, normalize_proxy_url, parse_proxy_chain_seed, proxy_for_country, proxy_label, set_proxy
from .session import CHATGPT_CLIENT_BUILD_NUMBER, CHATGPT_CLIENT_VERSION, DEFAULT_USER_AGENT, new_session


CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
UPDATE_PATH = "/backend-api/payments/checkout/update"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
RETRYABLE_STATUSES = {403, 429, 500, 502, 503, 504}
PAYMENT_COOKIE_NAMES = {
    "oai-did",
    "oai-hlib",
    "oai-sc",
    "oaicom-stable-id",
    "_account",
    "_account_is_fedramp",
    "__Secure-oai-is",
    "__Secure-next-auth.session-token",
    "__cf_bm",
    "__cflb",
    "_cfuvid",
    "__oailb",
    "cf_clearance",
}


class CardExtractionError(RuntimeError):
    pass


class CardUpstreamError(CardExtractionError):
    def __init__(self, status_code: int, message: str, *, retryable: bool = False, cloudflare: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.cloudflare = cloudflare


class InvalidPromotionError(CardExtractionError):
    pass


class NonZeroAmountError(CardExtractionError):
    pass


def _cfg_bool(ctx: ExtractionContext, primary: str, default: bool = False, alias: str = "") -> bool:
    value = ctx.cfg_bool(primary, default)
    return ctx.cfg_bool(alias, value) if alias else value


def _cfg_int(ctx: ExtractionContext, primary: str, default: int, minimum: int = 1, alias: str = "") -> int:
    value = ctx.cfg_int(primary, default, minimum=minimum)
    return ctx.cfg_int(alias, value, minimum=minimum) if alias else value


def _cfg_str(ctx: ExtractionContext, primary: str, default: str = "", alias: str = "") -> str:
    value = ctx.cfg_str(primary, default)
    return ctx.cfg_str(alias, value) if alias else value


def cookie_header_value(cookie_header: str, name: str) -> str:
    for item in str(cookie_header or "").split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key == name:
            return value.strip()
    return ""


def filter_payment_cookie_header(cookie_header: str) -> str:
    values: dict[str, str] = {}
    for item in str(cookie_header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name in PAYMENT_COOKIE_NAMES:
            values[name] = value
    return "; ".join(f"{name}={value}" for name, value in values.items())


def replace_cookie_value(cookie_header: str, name: str, value: str) -> str:
    values: list[str] = []
    replaced = False
    for item in str(cookie_header or "").split(";"):
        key, separator, _ = item.strip().partition("=")
        if not separator:
            continue
        if key == name:
            if not replaced:
                values.append(f"{name}={value}")
                replaced = True
        else:
            values.append(item.strip())
    if not replaced:
        values.insert(0, f"{name}={value}")
    return "; ".join(values)


def initial_cookie_header(session_cookie: str, device_id: str) -> str:
    raw_cookie = str(session_cookie or "").strip()
    if raw_cookie and "=" not in raw_cookie.split(";", 1)[0]:
        return f"oai-did={device_id}; __Secure-next-auth.session-token={raw_cookie}"
    filtered = filter_payment_cookie_header(raw_cookie)
    if not filtered:
        return f"oai-did={device_id}"
    return replace_cookie_value(filtered, "oai-did", device_id)


def merged_cookie_header(cookie_header: str, cookies: Any) -> str:
    values: dict[str, str] = {}
    for item in str(cookie_header or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            values[name] = value
    try:
        for name, value in cookies.items():
            if name:
                values[str(name)] = str(value)
    except Exception:
        pass
    return "; ".join(f"{name}={value}" for name, value in values.items())


def refresh_cookie_header(session: Any, fallback: str = "") -> None:
    headers = getattr(session, "headers", None)
    if headers is None:
        return
    cookie_header = filter_payment_cookie_header(
        merged_cookie_header(fallback or str(headers.get("Cookie") or ""), getattr(session, "cookies", None))
    )
    if cookie_header:
        headers["Cookie"] = cookie_header


def sync_cookies(target: Any, source: Any) -> None:
    if target is source:
        refresh_cookie_header(target)
        return
    try:
        target.cookies.update(source.cookies)
    except Exception:
        pass
    refresh_cookie_header(target, str(getattr(source, "headers", {}).get("Cookie") or ""))


def safe_close(session: Any) -> None:
    try:
        session and session.close()
    except Exception:
        pass


def money_minor_units(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for key in ("minorUnitsAmount", "minor_units_amount", "amount"):
            if value.get(key) is not None:
                return money_minor_units(value.get(key))
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def nested_value(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def checkout_amount_minor(data: Any) -> int | None:
    wrapper_keys = (
        "checkout_session",
        "checkoutSession",
        "session",
        "checkout",
        "data",
        "result",
        "payload",
        "response",
        "checkout_state",
        "checkoutState",
        "checkout_snapshot",
        "checkoutSnapshot",
    )
    amount_paths = (
        ("checkout_amount_minor",),
        ("total_summary", "due"),
        ("totalSummary", "due"),
        ("invoice", "amount_due"),
        ("invoice", "amountDue"),
        ("amount_due",),
        ("amountDue",),
        ("amount_total",),
        ("amountTotal",),
        ("total", "total"),
        ("total", "due"),
        ("total", "taxInclusive"),
        ("total", "taxInclusiveAmount"),
    )
    visited: set[int] = set()

    def find(payload: Any) -> int | None:
        if not isinstance(payload, dict) or id(payload) in visited:
            return None
        visited.add(id(payload))
        for path in amount_paths:
            amount = money_minor_units(nested_value(payload, path))
            if amount is not None:
                return amount
        line_items = payload.get("lineItems") or payload.get("line_items")
        if isinstance(line_items, list):
            total = 0
            found = False
            for item in line_items:
                if not isinstance(item, dict):
                    continue
                for key in ("total", "subtotal", "unitAmount", "unit_amount"):
                    amount = money_minor_units(item.get(key))
                    if amount is not None:
                        total += amount
                        found = True
                        break
            if found:
                return total
        for key in wrapper_keys:
            amount = find(payload.get(key))
            if amount is not None:
                return amount
        return None

    return find(data)


def checkout_currency(data: Any) -> str:
    visited: set[int] = set()

    def find(payload: Any) -> str:
        if not isinstance(payload, dict) or id(payload) in visited:
            return ""
        visited.add(id(payload))
        for key in ("currency", "currency_code", "currencyCode"):
            value = payload.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{3}", value.strip()):
                return value.strip().upper()
        for key in (
            "checkout_state",
            "checkoutState",
            "checkout_snapshot",
            "checkoutSnapshot",
            "checkout_session",
            "checkoutSession",
            "session",
            "checkout",
            "data",
            "result",
            "payload",
            "response",
            "total",
            "total_summary",
            "totalSummary",
        ):
            value = find(payload.get(key))
            if value:
                return value
        return ""

    return find(data)


def decode_react_router_payload(payload: Any) -> Any:
    if not isinstance(payload, list):
        return None
    resolved: dict[int, Any] = {}

    def resolve(reference: Any) -> Any:
        if not isinstance(reference, int):
            return reference
        if reference < 0 or reference >= len(payload):
            return None
        if reference in resolved:
            return resolved[reference]
        value = payload[reference]
        if isinstance(value, dict):
            output: dict[str, Any] = {}
            resolved[reference] = output
            for encoded_key, encoded_value in value.items():
                key_text = str(encoded_key)
                if not key_text.startswith("_") or not key_text[1:].isdigit():
                    continue
                key = resolve(int(key_text[1:]))
                if key is not None:
                    output[str(key)] = resolve(encoded_value)
            return output
        if isinstance(value, list):
            output_list: list[Any] = []
            resolved[reference] = output_list
            output_list.extend(resolve(item) for item in value)
            return output_list
        resolved[reference] = value
        return value

    return resolve(0)


def checkout_state_from_html(html: str) -> dict[str, Any]:
    def find_state(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            for key in ("checkout_state", "checkoutState"):
                state = payload.get(key)
                if isinstance(state, dict):
                    return state
            for value in payload.values():
                state = find_state(value)
                if state:
                    return state
            if checkout_amount_minor(payload) is not None and any(
                key in payload for key in ("total", "total_summary", "totalSummary", "lineItems", "line_items")
            ):
                return payload
        elif isinstance(payload, list):
            for value in payload:
                state = find_state(value)
                if state:
                    return state
        return {}

    def state_from_serialized(serialized: str) -> dict[str, Any]:
        try:
            decoded: Any = json.loads(serialized)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        if isinstance(decoded, list):
            state = find_state(decode_react_router_payload(decoded))
            if state:
                return state
        return find_state(decoded)

    enqueue_pattern = re.compile(
        r'window\.__reactRouterContext\.streamController\.enqueue\(("(?:\\.|[^"\\])*")\)',
        re.DOTALL,
    )
    chunks: list[str] = []
    for match in enqueue_pattern.finditer(str(html or "")):
        try:
            raw_payload = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(raw_payload, str):
            continue
        chunks.append(raw_payload)
        state = state_from_serialized(raw_payload)
        if state:
            return state
    if len(chunks) > 1:
        state = state_from_serialized("".join(chunks))
        if state:
            return state

    application_json_pattern = re.compile(
        r"<script\b[^>]*\btype=(['\"])application/json\1[^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in application_json_pattern.finditer(str(html or "")):
        state = state_from_serialized(html_unescape(match.group(2)).strip())
        if state:
            return state
    return {}


def payload_has_invalid_promotion(payload: Any) -> bool:
    if isinstance(payload, dict):
        return any(payload_has_invalid_promotion(value) for value in payload.values())
    if isinstance(payload, list):
        return any(payload_has_invalid_promotion(value) for value in payload)
    return isinstance(payload, str) and payload.strip().lower() == "invalid_promotion"


def extract_processor_entity(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("processor_entity") or payload.get("processorEntity")
    if direct:
        return str(direct).strip()
    for key in ("checkout_session", "checkoutSession", "session", "checkout", "data"):
        found = extract_processor_entity(payload.get(key))
        if found:
            return found
    return ""


def processor_entity_for_country(country: str, processor_entity: str = "") -> str:
    if str(processor_entity or "").strip():
        return str(processor_entity).strip()
    return "openai_llc" if normalize_country(country) == "US" else "openai_ie"


def checkout_short_url(cs_id: str, country: str, processor_entity: str = "") -> str:
    entity = processor_entity_for_country(country, processor_entity)
    return f"https://chatgpt.com/checkout/{entity}/{cs_id}"


def is_retryable_network_error(exc: Exception) -> bool:
    if any(item.__name__ in {"ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout", "SSLError"} for item in type(exc).mro()):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "read timed out",
            "connect timed out",
            "connection aborted",
            "proxy connect aborted",
            "curl: (56)",
            "unexpected_eof",
            "eof occurred in violation of protocol",
            "max retries exceeded",
        )
    )


def response_error(response: Any, prefix: str) -> CardUpstreamError:
    status = int(getattr(response, "status_code", 0) or 0)
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("content-type") or "")
    server = str(headers.get("server") or "")
    body = str(getattr(response, "text", "") or "")
    lowered = body[:4000].lower()
    is_html = "html" in content_type.lower() or body.lstrip().lower().startswith(("<!doctype", "<html"))
    cloudflare = status == 403 and (
        "challenge-platform" in lowered or "__cf$cv$params" in lowered or "cloudflare" in server.lower() or is_html
    )
    if is_html:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        detail = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "HTML verification page"
    else:
        detail = re.sub(r"\s+", " ", body).strip()[:300] or "empty response"
    return CardUpstreamError(
        status,
        f"{prefix}: HTTP {status}; {detail}",
        retryable=status in RETRYABLE_STATUSES,
        cloudflare=cloudflare,
    )


def rotate_proxy_session(proxy: str, country: str) -> str:
    proxy = normalize_proxy_url(proxy)
    if not proxy:
        return proxy
    parsed = urlsplit(proxy)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = parsed.hostname or ""
    if not username or not password or not hostname:
        return proxy
    rotated_username = re.sub(r"region-[A-Za-z]{2}", f"region-{country.upper()}", username)
    if re.search(r"sid-[A-Za-z0-9]+", rotated_username):
        rotated_username = re.sub(r"sid-[A-Za-z0-9]+", f"sid-{random.randint(10_000_000, 99_999_999)}", rotated_username)
    elif re.search(r"region-[A-Za-z]{2}", rotated_username):
        rotated_username += f"-sid-{random.randint(10_000_000, 99_999_999)}"
    else:
        return proxy
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote(rotated_username, safe='-._~')}:{quote(password, safe='-._~')}@{host}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def _card_billing_country(ctx: ExtractionContext) -> str:
    return normalize_country(_cfg_str(ctx, "card_billing_country", "PH", "CARD_BILLING_COUNTRY"))


def _card_currency(ctx: ExtractionContext) -> str:
    value = _cfg_str(ctx, "card_currency", "PHP", "CARD_CURRENCY").strip().upper()
    return value if re.fullmatch(r"[A-Z]{3}", value) else "PHP"


def _card_checkout_proxy_country(ctx: ExtractionContext) -> str:
    return normalize_country(_cfg_str(ctx, "card_checkout_proxy_country", "US", "CARD_CHECKOUT_PROXY_COUNTRY"))


def _card_update_proxy_country(ctx: ExtractionContext) -> str:
    return _card_update_proxy_countries(ctx)[0]


def _card_update_proxy_countries(ctx: ExtractionContext) -> list[str]:
    raw_alias = ctx.cfg_str("CARD_UPDATE_PROXY_COUNTRIES", "").strip()
    raw = raw_alias or ctx._cfg.get("card_update_proxy_countries", None)
    if raw is None:
        raw = _cfg_str(ctx, "card_update_proxy_country", "TR,JP", "CARD_UPDATE_PROXY_COUNTRY")
    if isinstance(raw, str):
        items = re.split(r"[,;\s]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = [str(raw)]

    countries: list[str] = []
    for item in items:
        country = normalize_country(str(item or ""))
        if re.fullmatch(r"[A-Z]{2}", country) and country not in countries:
            countries.append(country)
    return countries or ["TR", "JP"]


def _derive_proxy_for_country(proxy: str, country: str) -> str:
    try:
        return proxy_for_country(proxy, country)
    except Exception:
        return normalize_proxy_url(proxy)


def card_proxy_chain(ctx: ExtractionContext, proxy_seed: str) -> tuple[str, list[tuple[str, str]]]:
    explicit_chain = parse_proxy_chain_seed(proxy_seed)
    if explicit_chain:
        update_base = explicit_chain["promotion"]
        update_candidates = [
            (country, _derive_proxy_for_country(update_base, country))
            for country in _card_update_proxy_countries(ctx)
        ]
        return explicit_chain["checkout"], update_candidates
    checkout_proxy = proxy_for_country(proxy_seed, _card_checkout_proxy_country(ctx))
    update_candidates = [
        (country, proxy_for_country(proxy_seed, country))
        for country in _card_update_proxy_countries(ctx)
    ]
    return checkout_proxy, update_candidates


def log_card_proxy_chain(
    ctx: ExtractionContext,
    proxy_seed: str,
    checkout_proxy: str,
    update_candidates: list[tuple[str, str]],
) -> None:
    prefix = "explicit" if parse_proxy_chain_seed(proxy_seed) else "derived"
    update_labels = ", ".join(f"{country}={proxy_label(proxy)}" for country, proxy in update_candidates)
    ctx.log(
        f"Card proxy chain ({prefix}): "
        f"{_card_checkout_proxy_country(ctx)} checkout={proxy_label(checkout_proxy)}; "
        f"update[{update_labels}]"
    )


class CardCheckoutFlow:
    def __init__(self, ctx: ExtractionContext, access_token: str, session_token: str) -> None:
        self.ctx = ctx
        self.access_token = access_token
        self.session_token = session_token
        self.device_id = cookie_header_value(session_token, "oai-did") or str(uuid.uuid4())
        self.chatgpt_session_id = str(uuid.uuid4())

    def new_identity_session(self, proxy: str) -> Any:
        session = new_session(self.ctx, proxy)
        session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Authorization": f"Bearer {self.access_token}",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
                "Content-Type": "application/json",
                "oai-device-id": self.device_id,
                "oai-language": "en-US",
                "oai-session-id": self.chatgpt_session_id,
                "oai-client-version": CHATGPT_CLIENT_VERSION,
                "oai-client-build-number": CHATGPT_CLIENT_BUILD_NUMBER,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "sec-ch-ua": '"Google Chrome";v="136", "Not.A/Brand";v="8", "Chromium";v="136"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Cookie": initial_cookie_header(self.session_token, self.device_id),
            }
        )
        return session

    def clone_session(self, source: Any, proxy: str) -> Any:
        session = new_session(self.ctx, proxy)
        session.headers.update(dict(getattr(source, "headers", {})))
        try:
            session.cookies.update(source.cookies)
        except Exception:
            pass
        refresh_cookie_header(session, str(getattr(source, "headers", {}).get("Cookie") or ""))
        return session

    def probe_country(self, proxy: str, expected_country: str) -> None:
        session = new_session(self.ctx, proxy)
        try:
            response = session.get(TRACE_URL, timeout=min(_cfg_int(self.ctx, "card_timeout", 45, alias="CARD_TIMEOUT"), 12))
            if int(getattr(response, "status_code", 0) or 0) >= 400:
                raise response_error(response, "proxy country probe")
            fields = dict(line.split("=", 1) for line in str(getattr(response, "text", "") or "").splitlines() if "=" in line)
            observed = str(fields.get("loc") or "").upper()
            if observed != expected_country:
                raise CardExtractionError(f"proxy country mismatch: expected {expected_country}, observed {observed or 'unknown'}")
        finally:
            safe_close(session)

    def preflight(self, checkout_proxy: str, update_proxy: str, update_country: str) -> None:
        if not _cfg_bool(self.ctx, "card_verify_proxy_country", False, "CARD_VERIFY_PROXY_COUNTRY"):
            return
        with ThreadPoolExecutor(max_workers=2) as executor:
            checkout_future = executor.submit(self.probe_country, checkout_proxy, _card_checkout_proxy_country(self.ctx))
            update_future = executor.submit(self.probe_country, update_proxy, update_country)
            checkout_future.result()
            update_future.result()
        self.ctx.log(
            f"Card proxy check passed: checkout={_card_checkout_proxy_country(self.ctx)}, "
            f"update={update_country}."
        )

    def create_checkout(self, session: Any) -> dict[str, Any]:
        refresh_cookie_header(session)
        body = {
            "entry_point": _cfg_str(self.ctx, "card_entry_point", "all_plans_pricing_modal", "CARD_ENTRY_POINT") or "all_plans_pricing_modal",
            "plan_name": _cfg_str(self.ctx, "card_plan_name", "chatgptplusplan", "CARD_PLAN_NAME") or "chatgptplusplan",
            "billing_details": {
                "country": _card_billing_country(self.ctx),
                "currency": _card_currency(self.ctx),
            },
            "checkout_ui_mode": _cfg_str(self.ctx, "card_checkout_ui_mode", "custom", "CARD_CHECKOUT_UI_MODE") or "custom",
        }
        response = session.post(
            CHECKOUT_URL,
            json=body,
            headers={
                "Referer": "https://chatgpt.com/",
                "x-openai-target-path": "/backend-api/payments/checkout",
                "x-openai-target-route": "/backend-api/payments/checkout",
            },
            timeout=_cfg_int(self.ctx, "card_timeout", 45, alias="CARD_TIMEOUT"),
        )
        self.ctx.dump_http(response, "card_checkout", body, "POST", CHECKOUT_URL, force=response.status_code >= 400)
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            try:
                error_payload = response.json() or {}
            except Exception:
                error_payload = {}
            if str(error_payload.get("detail") or "").lower() == "user is already paid":
                raise CardUpstreamError(400, "the account is already paid and cannot create a Plus checkout")
            raise response_error(response, "card checkout create failed")
        try:
            payload = response.json() or {}
        except Exception as exc:
            raise CardUpstreamError(502, "card checkout create returned invalid JSON", retryable=True) from exc
        cs_id = str(payload.get("checkout_session_id") or payload.get("session_id") or payload.get("id") or "").strip()
        if not cs_id.startswith(("oaics_", "cs_")):
            raise CardUpstreamError(502, "card checkout response did not contain a supported session id")
        return {
            "cs_id": cs_id,
            "processor_entity": extract_processor_entity(payload),
            "billing_country": _card_billing_country(self.ctx),
            "currency": _card_currency(self.ctx),
            "checkout_amount_minor": checkout_amount_minor(payload),
        }

    def create_checkout_with_retry(self, checkout_proxy: str) -> tuple[Any, dict[str, Any]]:
        session = self.new_identity_session(checkout_proxy)
        current_proxy = checkout_proxy
        cloudflare_failures = 0
        exit_rotated = False
        last_error: Exception | None = None
        attempts = _cfg_int(self.ctx, "card_checkout_attempts", 10, alias="CARD_CHECKOUT_ATTEMPTS")
        for attempt in range(1, attempts + 1):
            self.ctx.log(f"Card checkout attempt {attempt}/{attempts}.")
            try:
                return session, self.create_checkout(session)
            except CardUpstreamError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    safe_close(session)
                    raise
                if exc.cloudflare:
                    cloudflare_failures += 1
                    if cloudflare_failures >= _cfg_int(self.ctx, "card_cf_same_identity_attempts", 5, alias="CARD_CF_SAME_IDENTITY_ATTEMPTS") and not exit_rotated:
                        rotated = rotate_proxy_session(current_proxy, _card_checkout_proxy_country(self.ctx))
                        if rotated != current_proxy:
                            current_proxy = rotated
                            set_proxy(session, current_proxy)
                            cloudflare_failures = 0
                            exit_rotated = True
                            self.ctx.log("Card checkout CF retries exhausted; rotated the proxy exit once.")
                time.sleep(max(0, _cfg_int(self.ctx, "card_cf_retry_delay", 3, minimum=0, alias="CARD_CF_RETRY_DELAY")))
            except Exception as exc:
                last_error = exc
                if not is_retryable_network_error(exc) or attempt >= attempts:
                    safe_close(session)
                    raise CardExtractionError(f"card checkout network error: {exc}") from exc
        safe_close(session)
        raise CardExtractionError(f"card checkout retries exhausted: {last_error}")

    def update_promotion(
        self,
        checkout_session: Any,
        checkout: dict[str, Any],
        checkout_proxy: str,
        update_proxy: str,
        update_country: str,
    ) -> dict[str, Any]:
        processor_entity = processor_entity_for_country(checkout["billing_country"], checkout.get("processor_entity", ""))
        body = {
            "checkout_session_id": checkout["cs_id"],
            "processor_entity": processor_entity,
            "plan_name": _cfg_str(self.ctx, "card_plan_name", "chatgptplusplan", "CARD_PLAN_NAME") or "chatgptplusplan",
            "price_interval": "month",
            "seat_quantity": 1,
            "promo_campaign": {
                "promo_campaign_id": _cfg_str(self.ctx, "card_promo_campaign_id", "plus-1-month-free", "CARD_PROMO_CAMPAIGN_ID") or "plus-1-month-free",
                "is_coupon_from_query_param": False,
            },
        }
        headers = {
            "Referer": checkout_short_url(checkout["cs_id"], checkout["billing_country"], processor_entity),
            "x-openai-target-path": UPDATE_PATH,
            "x-openai-target-route": UPDATE_PATH,
        }
        same_session = normalize_proxy_url(checkout_proxy) == normalize_proxy_url(update_proxy)
        session = checkout_session if same_session else self.clone_session(checkout_session, update_proxy)
        owns_session = not same_session
        current_proxy = update_proxy
        cloudflare_failures = 0
        exit_rotated = False
        attempts = _cfg_int(self.ctx, "card_update_attempts", 15, alias="CARD_UPDATE_ATTEMPTS")
        try:
            for attempt in range(1, attempts + 1):
                self.ctx.log(f"Card promotion update attempt {attempt}/{attempts}.")
                try:
                    refresh_cookie_header(session)
                    response = session.post(
                        f"https://chatgpt.com{UPDATE_PATH}",
                        json=body,
                        headers=headers,
                        timeout=_cfg_int(self.ctx, "card_timeout", 45, alias="CARD_TIMEOUT"),
                    )
                    self.ctx.dump_http(response, "card_checkout_update", body, "POST", f"https://chatgpt.com{UPDATE_PATH}", force=response.status_code >= 400)
                except Exception as exc:
                    if not is_retryable_network_error(exc) or attempt >= attempts:
                        raise CardExtractionError(f"card promotion update network error: {exc}") from exc
                    current_proxy = rotate_proxy_session(current_proxy, update_country)
                    if owns_session:
                        safe_close(session)
                    session = self.clone_session(checkout_session, current_proxy)
                    owns_session = True
                    continue
                if int(getattr(response, "status_code", 0) or 0) >= 400:
                    error = response_error(response, "card checkout/update failed")
                    if not error.retryable or attempt >= attempts:
                        raise error
                    if error.cloudflare:
                        cloudflare_failures += 1
                        if cloudflare_failures >= _cfg_int(self.ctx, "card_cf_same_identity_attempts", 5, alias="CARD_CF_SAME_IDENTITY_ATTEMPTS") and not exit_rotated:
                            rotated = rotate_proxy_session(current_proxy, update_country)
                            if rotated != current_proxy:
                                current_proxy = rotated
                                set_proxy(session, current_proxy)
                                cloudflare_failures = 0
                                exit_rotated = True
                                self.ctx.log("Card update CF retries exhausted; rotated the proxy exit once.")
                        time.sleep(max(0, _cfg_int(self.ctx, "card_cf_retry_delay", 3, minimum=0, alias="CARD_CF_RETRY_DELAY")))
                    else:
                        current_proxy = rotate_proxy_session(current_proxy, update_country)
                        if owns_session:
                            safe_close(session)
                        session = self.clone_session(checkout_session, current_proxy)
                        owns_session = True
                    continue
                try:
                    payload = response.json() or {}
                except Exception as exc:
                    raise CardUpstreamError(502, "card checkout/update returned invalid JSON") from exc
                if payload_has_invalid_promotion(payload):
                    raise InvalidPromotionError("card checkout/update returned invalid_promotion")
                if isinstance(payload, dict) and payload.get("success") is False:
                    raise CardUpstreamError(502, "card checkout/update explicitly rejected the promotion")
                sync_cookies(checkout_session, session)
                return payload
            raise CardExtractionError("card promotion update retries exhausted")
        finally:
            if owns_session:
                safe_close(session)

    def page_amount(self, session: Any, checkout: dict[str, Any]) -> tuple[int | None, str]:
        refresh_cookie_header(session)
        response = session.get(
            checkout_short_url(checkout["cs_id"], checkout["billing_country"], checkout.get("processor_entity", "")),
            headers={"Referer": "https://chatgpt.com/"},
            timeout=_cfg_int(self.ctx, "card_timeout", 45, alias="CARD_TIMEOUT"),
        )
        self.ctx.dump_http(response, "card_checkout_page", {}, "GET", checkout_short_url(checkout["cs_id"], checkout["billing_country"], checkout.get("processor_entity", "")), force=response.status_code >= 400)
        if int(getattr(response, "status_code", 0) or 0) >= 400:
            raise response_error(response, "card checkout page amount query failed")
        state = checkout_state_from_html(str(getattr(response, "text", "") or ""))
        return checkout_amount_minor(state), checkout_currency(state) or _card_currency(self.ctx)

    def verify_amount(self, checkout_session: Any, checkout: dict[str, Any], update_payload: dict[str, Any]) -> tuple[str, int | None, str]:
        amount = checkout_amount_minor(update_payload)
        currency = checkout_currency(update_payload) or _card_currency(self.ctx)
        if amount is None:
            try:
                amount, currency = self.page_amount(checkout_session, checkout)
            except CardUpstreamError as exc:
                self.ctx.log(f"Card amount page probe unavailable ({exc.status_code}); returning pending.", "[WARN] ")
                amount = None
        if amount is None:
            return "pending", None, currency
        if amount != 0 and _cfg_bool(self.ctx, "card_require_zero", True, "CARD_REQUIRE_ZERO"):
            raise NonZeroAmountError(f"card promotion did not produce a zero checkout: amount_minor={amount} {currency}")
        return "verified_zero" if amount == 0 else "non_zero_allowed", amount, currency

    def extract_once(self, checkout_proxy: str, update_proxy: str, update_country: str) -> str:
        checkout_session = None
        try:
            self.preflight(checkout_proxy, update_proxy, update_country)
            checkout_session, checkout = self.create_checkout_with_retry(checkout_proxy)
            self.ctx.log(f"Card checkout created: {checkout['cs_id'][:12]}...")
            update_payload = self.update_promotion(checkout_session, checkout, checkout_proxy, update_proxy, update_country)
            verification, amount, amount_currency = self.verify_amount(checkout_session, checkout, update_payload)
            result_url = checkout_short_url(checkout["cs_id"], checkout["billing_country"], checkout.get("processor_entity", ""))
            self.ctx.log(f"Card amount verification: {verification}; amount={amount}; currency={amount_currency}")
            return result_url
        finally:
            safe_close(checkout_session)


def run_card_attempt(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seed: str,
    attempt: int,
    max_retry: int,
) -> tuple[int, str, str]:
    ctx.log_context.prefix = f"[Card {attempt}/{max_retry}] "
    try:
        checkout_proxy, update_candidates = card_proxy_chain(ctx, proxy_seed)
        log_card_proxy_chain(ctx, proxy_seed, checkout_proxy, update_candidates)
        last_error = ""
        for update_country, update_proxy in update_candidates:
            ctx.log(f"Card update country attempt: {update_country}.")
            try:
                result_url = CardCheckoutFlow(ctx, access_token, session_token).extract_once(
                    checkout_proxy,
                    update_proxy,
                    update_country,
                )
                return attempt, result_url, ""
            except Exception as exc:
                last_error = str(exc)
                ctx.log(f"Card update country {update_country} failed: {last_error[:300]}", "[WARN] ")
        return attempt, "", last_error
    except Exception as exc:
        return attempt, "", str(exc)
    finally:
        ctx.log_context.prefix = ""


def run_card_single_link_parallel_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    card_retry = _cfg_int(ctx, "max_retry", 3, alias="CARD_MAX_RETRY")
    requested_workers = _cfg_int(ctx, "workers", 1, alias="CARD_WORKERS")
    worker_limit = _cfg_int(ctx, "workers_max", requested_workers, alias="CARD_WORKERS_MAX")
    workers = min(max(1, requested_workers), max(1, worker_limit), card_retry)
    if not proxy_seeds:
        raise RuntimeError("Card channel requires proxy seeds or custom proxy chains")
    seeds = [proxy_seeds[index % len(proxy_seeds)] for index in range(card_retry)]
    ctx.log(
        f"starting Card extraction: billing={_card_billing_country(ctx)}/{_card_currency(ctx)}, "
        f"proxy_chain={_card_checkout_proxy_country(ctx)}/{'|'.join(_card_update_proxy_countries(ctx))}, "
        f"card_retry={card_retry}, workers={workers}."
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(run_card_attempt, ctx, access_token, session_token, seed, index + 1, card_retry)
            for index, seed in enumerate(seeds)
        ]
        for future in as_completed(futures):
            attempt, result_url, error = future.result()
            if result_url:
                ctx.log(f"Card final payment URL: {result_url}")
                print(f"Card final payment URL:\n{result_url}")
                return 0
            ctx.log(f"Card extraction {attempt}/{card_retry} exception: {error[:300]}", "[WARN] ")
    return 1


def run_card_single_link_mode(
    ctx: ExtractionContext,
    access_token: str,
    session_token: str,
    proxy_seeds: list[str],
) -> int:
    card_workers = _cfg_int(ctx, "workers", 1, alias="CARD_WORKERS")
    if card_workers > 1:
        return run_card_single_link_parallel_mode(ctx, access_token, session_token, proxy_seeds)

    card_retry = _cfg_int(ctx, "max_retry", 3, alias="CARD_MAX_RETRY")
    if not proxy_seeds:
        raise RuntimeError("Card channel requires proxy seeds or custom proxy chains")
    seeds = [proxy_seeds[index % len(proxy_seeds)] for index in range(card_retry)]
    ctx.log(
        f"starting Card extraction: billing={_card_billing_country(ctx)}/{_card_currency(ctx)}, "
        f"proxy_chain={_card_checkout_proxy_country(ctx)}/{'|'.join(_card_update_proxy_countries(ctx))}, "
        f"card_retry={card_retry}."
    )
    for attempt, proxy_seed in enumerate(seeds, start=1):
        attempt_no, result_url, error = run_card_attempt(ctx, access_token, session_token, proxy_seed, attempt, card_retry)
        if result_url:
            ctx.log(f"Card final payment URL: {result_url}")
            print(f"Card final payment URL:\n{result_url}")
            return 0
        ctx.log(f"Card extraction {attempt_no}/{card_retry} ended without final URL: {error[:300]}", "[WARN] ")
    return 1
