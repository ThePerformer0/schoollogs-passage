"""Authentication, constant-time login checks, lockout, and session services (AUT-01..07)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import ipaddress

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from passage.models import AuditLog, User
from passage.repositories.auth_repo import (
    get_recent_failures_since_last_success,
    record_login_attempt,
)
from passage.services.audit import log_audit_event

# Strictly identical error message across nonexistent user, wrong password,
# inactive account, or locked (identifier, IP) pair (AUT-04, SEC-02).
UNIFORM_LOGIN_ERROR = "Identifiant ou mot de passe invalide."


@dataclass(frozen=True)
class AuthResult:
    """Outcome of a login attempt."""

    succeeded: bool
    user: User | None = None
    error_message: str = ""
    redirect_url: str = ""


@lru_cache(maxsize=1)
def _get_dummy_argon2_hash() -> str:
    """Generate a single in-memory Argon2id hash used for constant-time failure paths."""
    return make_password("dummy-timing-equalizer-never-matched")


def _equalize_timing(raw_password: str) -> None:
    """Run Argon2id verification on a dummy hash to prevent user enumeration by timing."""
    check_password(raw_password, _get_dummy_argon2_hash())


def get_client_ip(request: HttpRequest) -> str:
    """Extract client IP safely without trusting spoofable forwarding headers locally."""
    raw_ip = (request.META.get("REMOTE_ADDR") or "127.0.0.1").strip()
    try:
        return str(ipaddress.ip_address(raw_ip))
    except ValueError:
        return "127.0.0.1"


def get_home_url_for_user(user: User) -> str:
    """Return the dedicated landing route for the user's role (AUT-06)."""
    if user.role == User.Role.AGENT:
        return reverse("passage:agent_scan")
    if user.role == User.Role.MANAGER:
        return reverse("passage:manager_dashboard")
    return reverse("passage:parent_dashboard")


def is_login_locked(
    *,
    identifier: str,
    ip_address: str,
    now: datetime | None = None,
) -> bool:
    """Return True if (identifier, ip_address) exceeded MAX_TENTATIVES in FENETRE_TENTATIVES."""
    current_time = now or timezone.now()
    max_attempts = settings.PASSAGE_MAX_LOGIN_ATTEMPTS
    window = settings.PASSAGE_LOGIN_ATTEMPTS_WINDOW
    lockout_duration = settings.PASSAGE_LOGIN_LOCKOUT_DURATION

    recent_failures = get_recent_failures_since_last_success(
        identifier=identifier,
        ip_address=ip_address,
        limit=max_attempts,
    )
    if len(recent_failures) < max_attempts:
        return False

    newest_failure = recent_failures[0].attempted_at
    nth_oldest_failure = recent_failures[max_attempts - 1].attempted_at
    failures_within_window = (newest_failure - nth_oldest_failure) <= window
    lockout_still_active = current_time < (newest_failure + lockout_duration)
    return failures_within_window and lockout_still_active


def authenticate_and_login(
    request: HttpRequest,
    *,
    username: str,
    password: str,
) -> AuthResult:
    """Authenticate a user with lockout, constant-time failure handling, and audit logging."""
    cleaned_username = username.strip()
    ip_address = get_client_ip(request)
    now = timezone.now()

    if is_login_locked(identifier=cleaned_username, ip_address=ip_address, now=now):
        # Equalize timing so lockout is indistinguishable from invalid credentials (AUT-04).
        _equalize_timing(password)
        log_audit_event(
            action=AuditLog.Action.LOGIN_FAILURE,
            outcome=AuditLog.Outcome.DENIED,
            actor_identifier=cleaned_username,
            ip_address=ip_address,
            details="Blocage temporaire actif après échecs répétés.",
        )
        return AuthResult(succeeded=False, error_message=UNIFORM_LOGIN_ERROR)

    user = authenticate(request, username=cleaned_username, password=password)
    if user is None or not user.is_active:
        if user is not None and not user.is_active:
            _equalize_timing(password)
        record_login_attempt(
            identifier=cleaned_username,
            ip_address=ip_address,
            succeeded=False,
            attempted_at=now,
        )
        log_audit_event(
            action=AuditLog.Action.LOGIN_FAILURE,
            outcome=AuditLog.Outcome.FAILURE,
            actor_identifier=cleaned_username,
            ip_address=ip_address,
            details="Identifiants invalides.",
        )
        return AuthResult(succeeded=False, error_message=UNIFORM_LOGIN_ERROR)

    # Establish server-side session with explicit bounded lifetime (AUT-05, SEC-03).
    login(request, user)
    request.session.set_expiry(int(settings.PASSAGE_SESSION_DURATION.total_seconds()))

    record_login_attempt(
        identifier=cleaned_username,
        ip_address=ip_address,
        succeeded=True,
        attempted_at=now,
    )
    log_audit_event(
        action=AuditLog.Action.LOGIN_SUCCESS,
        outcome=AuditLog.Outcome.SUCCESS,
        actor=user,
        ip_address=ip_address,
    )
    return AuthResult(
        succeeded=True,
        user=user,
        redirect_url=get_home_url_for_user(user),
    )


def logout_user(request: HttpRequest) -> None:
    """Invalidate the current server session and log the logout event (AUT-05, AUD-01)."""
    user = request.user if request.user.is_authenticated else None
    ip_address = get_client_ip(request)
    if user is not None:
        log_audit_event(
            action=AuditLog.Action.LOGOUT,
            outcome=AuditLog.Outcome.SUCCESS,
            actor=user,
            ip_address=ip_address,
        )
    # django.contrib.auth.logout flushes the session from the server store.
    logout(request)
