from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import requests


CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


def build_payload(country: str, currency: str, promo_id: str) -> dict[str, Any]:
    return {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": country, "currency": currency},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "custom",
        "promo_campaign": {
            "promo_campaign_id": promo_id,
            "is_coupon_from_query_param": False,
        },
    }


def build_headers(access_token: str) -> dict[str, str]:
    device_id = str(uuid.uuid4())
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Authorization": f"Bearer {access_token}",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Content-Type": "application/json",
        "oai-device-id": device_id,
        "oai-language": "ko-KR",
        "oai-session-id": device_id,
        "x-openai-target-path": "/backend-api/payments/checkout",
        "x-openai-target-route": "/backend-api/payments/checkout",
    }


def redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    safe = dict(headers)
    if safe.get("Authorization"):
        safe["Authorization"] = "Bearer <redacted>"
    return safe


def read_token(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Test ChatGPT checkout probe request shape.")
    parser.add_argument("--token-file", default=".tmp/at.txt", help="Path to a file containing the access token.")
    parser.add_argument("--country", default="KR")
    parser.add_argument("--currency", default="KRW")
    parser.add_argument("--promo-id", default="plus-1-month-free")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--dry-run", action="store_true", help="Only print request shape; do not send the request.")
    args = parser.parse_args()

    payload = build_payload(args.country.upper(), args.currency.upper(), args.promo_id)
    token_path = Path(args.token_file)
    access_token = read_token(token_path)
    headers = build_headers(access_token or "<missing>")

    print("URL:")
    print(CHECKOUT_URL)
    print("\nHeaders:")
    print(json.dumps(redacted_headers(headers), ensure_ascii=False, indent=2))
    print("\nPayload:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("\nDry run only: request not sent.")
        return 0

    if not access_token:
        print(f"\nMissing token file: {token_path}", file=sys.stderr)
        print("Create .tmp/at.txt with the AT, then run without --dry-run.", file=sys.stderr)
        return 2

    response = requests.post(CHECKOUT_URL, headers=headers, json=payload, timeout=args.timeout)
    print("\nResponse:")
    print(f"HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        print(response.text[:1000])
        return 1 if response.status_code >= 400 else 0

    summary = {
        "one_click_trial_eligible": body.get("one_click_trial_eligible") if isinstance(body, dict) else None,
        "checkout_session_id": body.get("checkout_session_id") or body.get("session_id") or body.get("id") if isinstance(body, dict) else None,
        "processor_entity": body.get("processor_entity") if isinstance(body, dict) else None,
        "error": body.get("error") or body.get("message") if isinstance(body, dict) else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if response.status_code >= 400:
        print("\nRaw error preview:")
        print(json.dumps(body, ensure_ascii=False)[:1000])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
