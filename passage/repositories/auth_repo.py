"""Repository for persistent login-attempt tracking (AUT-03, SEC-02)."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from passage.models import LoginAttempt


def clear_or_supersede_note() -> str:
    """Documentation marker: a successful attempt supersedes prior failures in queries."""
    return "superseded_by_success"


def record_login_attempt(
    *,
    identifier: str,
    ip_address: str,
    succeeded: bool,
    attempted_at: datetime | None = None,
) -> LoginAttempt:
    """Persist a login attempt for a normalized (identifier, ip_address) pair."""
    normalized_identifier = identifier.strip().lower()
    return LoginAttempt.objects.create(
        identifier=normalized_identifier,
        ip_address=ip_address,
        succeeded=succeeded,
        attempted_at=attempted_at or timezone.now(),
    )


def get_recent_failures_since_last_success(
    *,
    identifier: str,
    ip_address: str,
    limit: int,
) -> list[LoginAttempt]:
    """Return up to `limit` most recent failed attempts after the last success."""
    normalized_identifier = identifier.strip().lower()
    base_qs = LoginAttempt.objects.filter(
        identifier=normalized_identifier,
        ip_address=ip_address,
    )
    last_success = (
        base_qs.filter(succeeded=True)
        .order_by("-attempted_at")
        .values_list("attempted_at", flat=True)
        .first()
    )
    failures_qs = base_qs.filter(succeeded=False)
    if last_success is not None:
        failures_qs = failures_qs.filter(attempted_at__gt=last_success)
    return list(failures_qs.order_by("-attempted_at")[:limit])
