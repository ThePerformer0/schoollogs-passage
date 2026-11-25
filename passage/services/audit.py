"""Security audit logging service (AUT-07, AUD-01, AUD-03)."""

from __future__ import annotations

from passage.models import AuditLog, School, User


def log_audit_event(
    *,
    action: str,
    outcome: str,
    actor: User | None = None,
    actor_identifier: str = "",
    school: School | None = None,
    target_type: str = "",
    target_id: str | int = "",
    ip_address: str | None = None,
    details: str = "",
) -> AuditLog:
    """Create a minimal audit entry without ever persisting secrets (AUD-03)."""
    resolved_actor = actor if (actor is not None and actor.is_authenticated) else None
    resolved_identifier = (
        actor_identifier.strip()[:150]
        if actor_identifier
        else (resolved_actor.username if resolved_actor else "")
    )
    resolved_school = school
    if resolved_school is None and resolved_actor is not None and resolved_actor.school_id:
        resolved_school = resolved_actor.school

    return AuditLog.objects.create(
        school=resolved_school,
        actor=resolved_actor,
        actor_identifier=resolved_identifier,
        action=action,
        target_type=target_type[:64],
        target_id=str(target_id)[:64] if target_id != "" else "",
        outcome=outcome,
        ip_address=ip_address,
        details=details[:255],
    )
