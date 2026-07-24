"""HTTP session management — curl_cffi with graceful fallback to requests.

Creates sessions with optional proxy and pre-proxy (Kookeey chain) support.
All mutation of ExtractionContext (redaction, logging) stays in the
ExtractionContext class; this module only deals with HTTP session creation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import requests

try:
    from curl_cffi import CurlOpt
    from curl_cffi.requests import Session as CurlCffiSession
except ImportError:
    CurlOpt = None
    CurlCffiSession = None

if TYPE_CHECKING:
    from .context import ExtractionContext

from .proxy import set_proxy

# ── constants ──────────────────────────────────────────────────────────────

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
)

CHATGPT_CLIENT_VERSION = "prod-db390ebea64862bf1899c420a4c736e0cf639747"
CHATGPT_CLIENT_BUILD_NUMBER = "7904904"

DEFAULT_TIMEOUT = 30
CHATGPT_TIMEOUT = 45


# ── session factory ────────────────────────────────────────────────────────

def new_session(
    ctx: ExtractionContext,
    proxy: str = "",
    *,
    use_pre_proxy: bool = True,
) -> Any:
    """Create an HTTP session with optional proxy chain support.

    When curl_cffi is available, uses CurlCffiSession with Chrome 136
    impersonation (required for Kookeey proxies that block non-browser
    TLS fingerprints).  When a pre-proxy is configured, it is set via
    CurlOpt.PRE_PROXY for chain routing.

    Args:
        ctx: Provides pre_proxy_url() and register_proxy_for_redaction().
        proxy: Target proxy URL (goes through session.proxies).
        use_pre_proxy: When True (default), the pre-proxy from
            ctx.pre_proxy_url() is applied as CurlOpt.PRE_PROXY.

    Returns:
        A requests.Session or CurlCffiSession ready for use.

    Raises:
        RuntimeError: If a pre-proxy is requested but curl_cffi is
            not installed.
    """
    pre_proxy = ctx.pre_proxy_url() if use_pre_proxy else ""
    if pre_proxy:
        ctx.register_proxy_for_redaction(pre_proxy)

    if CurlCffiSession is not None:
        kwargs: dict[str, Any] = {"impersonate": "chrome136"}
        if pre_proxy:
            if CurlOpt is None:
                raise RuntimeError("本机前置代理需要 curl_cffi 支持")
            kwargs["curl_options"] = {CurlOpt.PRE_PROXY: pre_proxy}
        session = CurlCffiSession(**kwargs)
    else:
        if pre_proxy:
            raise RuntimeError(
                "本机前置代理需要 curl_cffi：python3 -m pip install curl_cffi"
            )
        session = requests.Session()

    if hasattr(session, "trust_env"):
        session.trust_env = False
    if proxy:
        set_proxy(session, proxy)

    return session
