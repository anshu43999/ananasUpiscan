from __future__ import annotations

import os
from typing import Any

import requests


def account_check_url() -> str:
    return os.environ.get("ACCOUNT_CHECK_API_URL", "https://cha.nerver.cc/api/v1/check").strip()


def check_account_eligibility(token: str, promo_id: str = "plus-1-month-free") -> dict[str, Any]:
    try:
        response = requests.post(
            account_check_url(),
            json={"token": token, "promoId": promo_id},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=float(os.environ.get("ACCOUNT_CHECK_TIMEOUT", "30")),
        )
    except requests.RequestException as exc:
        return {
            "token_ok": False,
            "eligible": False,
            "promo_id": promo_id,
            "error": str(exc),
        }

    try:
        payload: dict[str, Any] = response.json()
    except ValueError:
        payload = {
            "token_ok": False,
            "eligible": False,
            "promo_id": promo_id,
            "error": response.text[:500],
        }

    payload.setdefault("status", response.status_code)
    payload.setdefault("promo_id", promo_id)
    if response.status_code >= 400 and not payload.get("error"):
        payload["error"] = f"account eligibility check failed ({response.status_code})"
    return payload
