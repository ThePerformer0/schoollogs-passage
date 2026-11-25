"""Server-side role and resource access control decorators (ROL-01, SEC-04, SEC-05)."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import urlencode

from passage.models import AuditLog
from passage.services.audit import log_audit_event
from passage.services.auth import get_client_ip


def deny_access(
    request: HttpRequest,
    *,
    target_type: str = "",
    target_id: str | int = "",
    details: str = "Accès refusé.",
) -> HttpResponseForbidden:
    """Log an access-denial audit event and return a generic 403 without data leakage."""
    log_audit_event(
        action=AuditLog.Action.ACCESS_DENIED,
        outcome=AuditLog.Outcome.DENIED,
        actor=request.user if request.user.is_authenticated else None,
        target_type=target_type,
        target_id=target_id,
        ip_address=get_client_ip(request),
        details=details,
    )
    return HttpResponseForbidden("Accès refusé.")


def role_required(*allowed_roles: str) -> Callable:
    """Enforce authentication and strict server-side role membership on every request."""

    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            if not request.user.is_authenticated:
                query = urlencode({"next": request.get_full_path()})
                return redirect(f"{settings.LOGIN_URL}?{query}")

            if not request.user.is_active or request.user.role not in allowed_roles:
                return deny_access(
                    request,
                    target_type="route",
                    target_id=request.path,
                    details=f"Rôle non autorisé ({request.user.role}).",
                )

            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
