import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("delx-therapist")

_SENTRY_READY = False
_SENTRY_ATTEMPTED = False
_MESSAGE_LAST_SENT: dict[str, float] = {}

try:
    import sentry_sdk
    from sentry_sdk.integrations.starlette import StarletteIntegration
except Exception:  # pragma: no cover - optional dependency
    sentry_sdk = None
    StarletteIntegration = None


def _float_env(name: str, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def init_sentry(*, service_name: str, service_version: str) -> bool:
    global _SENTRY_READY, _SENTRY_ATTEMPTED
    if _SENTRY_ATTEMPTED:
        return _SENTRY_READY
    _SENTRY_ATTEMPTED = True

    dsn = str(os.getenv("SENTRY_DSN", "")).strip()
    if not dsn:
        return False
    if sentry_sdk is None:
        logger.warning("SENTRY_DSN is set but sentry_sdk is not installed")
        return False

    integrations: list[Any] = []
    if StarletteIntegration is not None:
        integrations.append(StarletteIntegration(transaction_style="url"))

    sentry_sdk.init(
        dsn=dsn,
        environment=str(os.getenv("SENTRY_ENVIRONMENT", "production")).strip() or "production",
        release=str(os.getenv("SENTRY_RELEASE", service_version)).strip() or service_version,
        traces_sample_rate=_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.0),
        profiles_sample_rate=_float_env("SENTRY_PROFILES_SAMPLE_RATE", 0.0),
        send_default_pii=False,
        max_breadcrumbs=int(_float_env("SENTRY_MAX_BREADCRUMBS", 100)),
        integrations=integrations,
    )
    sentry_sdk.set_tag("service", service_name)
    sentry_sdk.set_tag("service_version", service_version)
    _SENTRY_READY = True
    logger.info("Sentry initialized for %s", service_name)
    return True


@contextmanager
def sentry_scope(*, tags: dict[str, object] | None = None, extras: dict[str, object] | None = None) -> Iterator[None]:
    if not _SENTRY_READY or sentry_sdk is None:
        yield
        return
    with sentry_sdk.push_scope() as scope:
        for key, value in (tags or {}).items():
            if value is not None:
                scope.set_tag(key, value)
        for key, value in (extras or {}).items():
            if value is not None:
                scope.set_extra(key, value)
        yield


def capture_exception(
    exc: BaseException,
    *,
    tags: dict[str, object] | None = None,
    extras: dict[str, object] | None = None,
) -> None:
    if not _SENTRY_READY or sentry_sdk is None:
        return
    with sentry_scope(tags=tags, extras=extras):
        sentry_sdk.capture_exception(exc)


def capture_message(
    message: str,
    *,
    level: str = "warning",
    tags: dict[str, object] | None = None,
    extras: dict[str, object] | None = None,
    fingerprint: list[str] | None = None,
    cooldown_key: str | None = None,
    cooldown_seconds: float | None = None,
) -> bool:
    if not _SENTRY_READY or sentry_sdk is None:
        return False

    key = str(cooldown_key or "").strip()
    if key:
        window = cooldown_seconds
        if window is None:
            window = _float_env("SENTRY_MESSAGE_COOLDOWN_SECONDS", 900.0)
        now = time.time()
        last_sent = _MESSAGE_LAST_SENT.get(key, 0.0)
        if window > 0 and now - last_sent < window:
            return False
        _MESSAGE_LAST_SENT[key] = now

    with sentry_sdk.push_scope() as scope:
        for tag_key, tag_value in (tags or {}).items():
            if tag_value is not None:
                scope.set_tag(tag_key, tag_value)
        for extra_key, extra_value in (extras or {}).items():
            if extra_value is not None:
                scope.set_extra(extra_key, extra_value)
        if fingerprint:
            scope.fingerprint = [str(item) for item in fingerprint if str(item or "").strip()]
        sentry_sdk.capture_message(str(message or "").strip() or "delx-structured-warning", level=level)
    return True
