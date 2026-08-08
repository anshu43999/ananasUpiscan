from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, MutableMapping


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_time(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def trim_sequence(value: list[Any], max_items: int) -> None:
    if max_items <= 0 or len(value) <= max_items:
        return
    del value[: len(value) - max_items]


def prune_jobs(
    jobs: MutableMapping[str, Any],
    *,
    max_jobs: int,
    ttl_seconds: int,
) -> None:
    if not jobs:
        return
    cutoff = utc_now() - timedelta(seconds=max(60, ttl_seconds))
    for job_id, job in list(jobs.items()):
        status = str(getattr(job, "status", "") or "")
        updated_at = parse_iso_time(str(getattr(job, "updated_at", "") or ""))
        if status in TERMINAL_STATUSES and updated_at and updated_at < cutoff:
            jobs.pop(job_id, None)

    if len(jobs) <= max_jobs:
        return
    ordered = sorted(
        jobs.items(),
        key=lambda pair: parse_iso_time(str(getattr(pair[1], "updated_at", "") or "")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    overflow = len(jobs) - max(1, max_jobs)
    removed = 0
    for job_id, job in ordered:
        if removed >= overflow:
            break
        if str(getattr(job, "status", "") or "") in TERMINAL_STATUSES:
            jobs.pop(job_id, None)
            removed += 1
