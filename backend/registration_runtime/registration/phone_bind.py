"""Phone binding callback support for UPIScan OAuth resume jobs.

This is the lightweight migration of the reference project's phone-bind
adapter. It keeps the callback contract used by patch_resume_bind, but avoids
the reference dashboard resource-pool dependency.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.base_sms import BaseSmsProvider, PhoneCallbackController, _HERO_SMS_VERIFY_LOCK


class BindPhoneCallbackController(PhoneCallbackController):
    def __init__(self, provider_key: str, config: dict[str, Any], *, service: str, country: str = "", log_fn: Callable[[str], None] | None = None):
        super().__init__(provider_key, config, service=service, country=country, log_fn=log_fn)
        self.phone_submitted = False
        self.code_obtained = False
        self.bind_failure_reason = ""

    def __call__(self) -> str:
        expecting_code = self.phase == "need_code" and self.activation is not None
        try:
            value = super().__call__()
        except Exception as exc:
            if expecting_code:
                self.bind_failure_reason = str(exc)
            raise
        if expecting_code and value:
            self.code_obtained = True
        return value

    def mark_send_failed(self, reason: str = "") -> None:
        self.bind_failure_reason = str(reason or "")
        super().mark_send_failed(reason)

    def mark_send_succeeded(self) -> None:
        self.phone_submitted = True
        super().mark_send_succeeded()

    def mark_code_failed(self, reason: str = "") -> None:
        self.code_obtained = True
        self.bind_failure_reason = str(reason or "")
        super().mark_code_failed(reason)

    def report_success(self) -> None:
        self.phone_submitted = True
        self.code_obtained = True
        super().report_success()
        self.bind_failure_reason = ""

    def cleanup(self) -> None:
        if self.activation and not self.completed:
            try:
                provider = self._provider()
                activation_id = self.activation.activation_id
                hook = getattr(provider, "mark_attempt_failed", None)
                hook_defined = callable(hook) and getattr(type(provider), "mark_attempt_failed", None) is not BaseSmsProvider.mark_attempt_failed
                if hook_defined:
                    phase = "otp" if self.code_obtained else "send" if self.phone_submitted else "cleanup"
                    hook(activation_id, outcome=f"bind_phone_{phase}_failed", failure_code=phase, reason=self.bind_failure_reason or "phone bind flow aborted")
                    self.log(f"已回写绑定手机号失败: activation_id={activation_id} phase={phase}")
                else:
                    provider.cancel(activation_id)
                    self.log(f"已释放未完成绑定手机号: activation_id={activation_id}")
            except Exception:
                pass
        if self._verify_lock_acquired:
            _HERO_SMS_VERIFY_LOCK.release()
            self._verify_lock_acquired = False


def _provider_alias(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "user": "user_phone_url",
        "manual": "user_phone_url",
        "phone_url": "user_phone_url",
        "bind_user_phone_url": "user_phone_url",
        "sms-activate": "sms_activate",
        "smsactivate": "sms_activate",
        "hero": "herosms",
        "hero_sms": "herosms",
        "sms_bower": "smsbower",
    }
    return aliases.get(text, text)


def create_binding_phone_callback(config: dict[str, Any], *, log_fn: Callable[[str], None]) -> tuple[Any | None, Callable[[], None]]:
    """Build the optional add-phone callback for OAuth resume.

    Returns (None, noop) when no bind SMS provider is configured, so callers can
    pass it directly into patch_resume_bind without allocating a number up front.
    """
    raw_provider = str(config.get("bind_sms_provider") or "").strip()
    provider_key = _provider_alias(raw_provider)
    if not provider_key:
        return None, lambda: None

    bind_config = dict(config or {})
    use_resource_pool = bool(config.get("bind_use_resource_pool")) or raw_provider.strip().lower() in {"pool", "resource_pool", "bind_user_phone_url"}
    if use_resource_pool:
        provider_key = "user_phone_url"
        bind_config["_resource_provider"] = str(config.get("bind_resource_provider") or "bind_user_phone_url").strip() or "bind_user_phone_url"
        bind_config["dashboard_task_id"] = str(config.get("dashboard_task_id") or config.get("task_id") or "")
    key_map = {
        "bind_sms_phone_url": "sms_phone_url",
        "bind_sms_phone_urls": "sms_phone_urls",
        "bind_sms_phone_url_file": "sms_phone_url_file",
        "bind_sms_proxy": "sms_proxy",
        "bind_sms_api_key": "sms_api_key",
        "bind_sms_service": "sms_service",
        "bind_sms_country": "sms_country",
        "bind_country_code": "country_code",
        "bind_country_name": "country_name",
        "bind_herosms_api_key": "herosms_api_key",
        "bind_herosms_service": "herosms_service",
        "bind_herosms_country": "herosms_country",
        "bind_herosms_max_price": "herosms_max_price",
        "bind_smsbower_api_key": "smsbower_api_key",
        "bind_smsbower_service": "smsbower_service",
        "bind_smsbower_country": "smsbower_country",
        "bind_smsbower_max_price": "smsbower_max_price",
        "bind_smsbower_min_price": "smsbower_min_price",
        "bind_smsbower_provider_ids": "smsbower_provider_ids",
        "bind_sms_activate_api_key": "sms_activate_api_key",
        "bind_sms_activate_country": "sms_activate_country",
    }
    for source, target in key_map.items():
        value = config.get(source)
        if str(value or "").strip():
            bind_config[target] = value

    generic_key = str(bind_config.get("bind_sms_api_key") or bind_config.get("sms_api_key") or "").strip()
    if generic_key and provider_key in {"herosms", "herosms_api"}:
        bind_config["herosms_api_key"] = generic_key
    if generic_key and provider_key in {"smsbower", "smsbower_api"}:
        bind_config["smsbower_api_key"] = generic_key
    if generic_key and provider_key in {"sms_activate", "sms_activate_api"}:
        bind_config["sms_activate_api_key"] = generic_key

    service = str(config.get("bind_sms_service") or bind_config.get("sms_service") or "dr").strip()
    country = str(config.get("bind_sms_country") or bind_config.get("sms_country") or "").strip()
    controller = BindPhoneCallbackController(
        provider_key,
        bind_config,
        service=service,
        country=country,
        log_fn=log_fn,
    )
    return controller, controller.cleanup
