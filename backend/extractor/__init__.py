# extractor — UPI payment link extraction engine (modular)
#
# Refactored from upi_extract.py: all shared state lives in ExtractionContext,
# constants live in config, and business logic is split across modules:
#   context   — ExtractionContext (per-job state container)
#   config    — constants, regexes, billing profiles
#   logging   — log(), dump_http(), redaction
#   proxy     — proxy chain, state mgmt, zero cache, ordering
#   session   — curl_cffi session factory, ChatGPT session builder
#   checkout  — ChatGPT checkout creation, promotion, taxes, snapshot
#   provider  — Stripe init, confirm, approve, poll, run_provider_flow
#   extract   — run_once, run_attempt, single-link modes, result extraction
#
# CLI entry point (backward compat):
#   python -m backend.extractor  →  same as original if __name__ == "__main__"
