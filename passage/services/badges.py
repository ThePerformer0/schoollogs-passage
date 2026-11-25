"""Rotating digital badge crypto, lifecycle transitions, and one-time activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import math
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from passage.models import (
    AuditLog,
    Badge,
    BadgeActivationLink,
    BadgeStatusHistory,
    Student,
    User,
)
from passage.services.audit import log_audit_event

CODE_VERSION = "v1"
SIGNATURE_HEX_LENGTH = 32  # 128-bit truncated HMAC-SHA256


class BadgeError(Exception):
    """Base domain error for badge operations."""


class BadgeTransitionError(BadgeError):
    """Raised when an invalid badge status transition is requested."""


class ActivationLinkError(BadgeError):
    """Raised when an activation link is unknown, expired, already used, or inactive."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParsedBadgeCode:
    """Parsed components of a rotating QR badge payload."""

    version: str
    public_id: str
    time_step: int
    signature: str


def compute_time_step(reference_time: datetime | None = None, *, step_seconds: int | None = None) -> int:
    """Compute the discrete time step floor(epoch_utc / PAS_CODE)."""
    now = reference_time or timezone.now()
    step = step_seconds if step_seconds is not None else settings.PASSAGE_CODE_STEP_SECONDS
    return math.floor(now.timestamp() / step)


def sign_badge_code(*, public_id: str, secret: str, time_step: int) -> str:
    """Generate a rotating badge code: v1.<public_id>.<time_step>.<hmac_hex_32>."""
    message = f"{CODE_VERSION}.{public_id}.{time_step}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    signature = digest[:SIGNATURE_HEX_LENGTH]
    return f"{CODE_VERSION}.{public_id}.{time_step}.{signature}"


def generate_current_badge_code(badge: Badge, *, reference_time: datetime | None = None) -> str:
    """Generate the signed rotating code for `badge` at `reference_time`."""
    step = compute_time_step(reference_time)
    return sign_badge_code(public_id=badge.public_id, secret=badge.secret, time_step=step)


def parse_badge_code(raw_code: str) -> ParsedBadgeCode | None:
    """Parse and validate the syntactic structure of a scanned badge payload."""
    if not isinstance(raw_code, str):
        return None
    cleaned = raw_code.strip()
    parts = cleaned.split(".")
    if len(parts) != 4:
        return None
    version, public_id, raw_step, signature = parts
    if version != CODE_VERSION:
        return None
    if not public_id or len(public_id) > 64:
        return None
    if len(signature) != SIGNATURE_HEX_LENGTH:
        return None
    try:
        int(signature, 16)
        time_step = int(raw_step)
    except ValueError:
        return None
    if time_step < 0:
        return None
    return ParsedBadgeCode(
        version=version,
        public_id=public_id,
        time_step=time_step,
        signature=signature.lower(),
    )


def verify_badge_signature(*, badge: Badge, parsed: ParsedBadgeCode) -> bool:
    """Verify the HMAC-SHA256 signature in constant time (BAD-02)."""
    if parsed.public_id != badge.public_id:
        return False
    expected_code = sign_badge_code(
        public_id=badge.public_id,
        secret=badge.secret,
        time_step=parsed.time_step,
    )
    expected_signature = expected_code.rsplit(".", 1)[-1]
    return hmac.compare_digest(expected_signature, parsed.signature)


def is_time_step_fresh(
    *,
    code_time_step: int,
    retained_at: datetime,
    step_seconds: int | None = None,
    tolerance_steps: int | None = None,
) -> bool:
    """Check whether `code_time_step` falls within +/- TOLERANCE_PAS of `retained_at` (BAD-04)."""
    expected_step = compute_time_step(retained_at, step_seconds=step_seconds)
    tolerance = (
        tolerance_steps
        if tolerance_steps is not None
        else settings.PASSAGE_CODE_STEP_TOLERANCE
    )
    return abs(code_time_step - expected_step) <= tolerance


def hash_activation_token(raw_token: str) -> str:
    """Compute SHA-256 hex digest of a raw one-time activation token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def issue_badge(
    student: Student,
    *,
    actor: User | None,
    reason: str = "",
    ip_address: str | None = None,
) -> Badge:
    """Issue a fresh active badge for `student`, immediately revoking any prior non-terminal badge."""
    now = timezone.now()
    existing_non_terminal = list(
        Badge.objects.select_for_update()
        .filter(
            student=student,
            status__in=[Badge.Status.ACTIVE, Badge.Status.SUSPENDED],
        )
        .order_by("-issued_at")
    )

    replaced_badge: Badge | None = None
    for old_badge in existing_non_terminal:
        previous_status = old_badge.status
        old_badge.status = Badge.Status.REVOKED
        old_badge.save(update_fields=["status"])
        BadgeStatusHistory.objects.create(
            badge=old_badge,
            changed_by=actor,
            previous_status=previous_status,
            new_status=Badge.Status.REVOKED,
            reason=reason or "Réémission d'un nouveau badge.",
            changed_at=now,
        )
        log_audit_event(
            action=AuditLog.Action.BADGE_STATUS_CHANGED,
            outcome=AuditLog.Outcome.SUCCESS,
            actor=actor,
            school=student.school,
            target_type="badge",
            target_id=old_badge.public_id,
            ip_address=ip_address,
            details=f"{previous_status} -> {Badge.Status.REVOKED} (réémission)",
        )
        if replaced_badge is None:
            replaced_badge = old_badge

    new_badge = Badge.objects.create(
        student=student,
        status=Badge.Status.ACTIVE,
        issued_at=now,
        replaced_badge=replaced_badge,
    )
    BadgeStatusHistory.objects.create(
        badge=new_badge,
        changed_by=actor,
        previous_status="",
        new_status=Badge.Status.ACTIVE,
        reason=reason or ("Réémission de badge." if replaced_badge else "Émission initiale."),
        changed_at=now,
    )
    log_audit_event(
        action=AuditLog.Action.BADGE_STATUS_CHANGED,
        outcome=AuditLog.Outcome.SUCCESS,
        actor=actor,
        school=student.school,
        target_type="badge",
        target_id=new_badge.public_id,
        ip_address=ip_address,
        details=f"emission -> {Badge.Status.ACTIVE}",
    )
    return new_badge


@transaction.atomic
def change_badge_status(
    badge: Badge,
    *,
    new_status: str,
    actor: User | None,
    reason: str = "",
    ip_address: str | None = None,
) -> Badge:
    """Transition a badge's status while enforcing terminal states and full traceability (BAD-05)."""
    locked_badge = (
        Badge.objects.select_for_update()
        .select_related("student", "student__school")
        .get(pk=badge.pk)
    )
    valid_statuses = {choice.value for choice in Badge.Status}
    if new_status not in valid_statuses:
        raise BadgeTransitionError("État de badge non reconnu.")

    previous_status = locked_badge.status
    if previous_status == new_status:
        raise BadgeTransitionError("Le badge est déjà dans cet état.")

    if previous_status in Badge.TERMINAL_STATUSES:
        raise BadgeTransitionError(
            "Un badge perdu ou révoqué est définitif et ne peut plus changer d'état."
        )

    locked_badge.status = new_status
    try:
        locked_badge.save(update_fields=["status"])
    except IntegrityError as exc:
        raise BadgeTransitionError(
            "Cet élève possède déjà un autre badge actif."
        ) from exc

    BadgeStatusHistory.objects.create(
        badge=locked_badge,
        changed_by=actor,
        previous_status=previous_status,
        new_status=new_status,
        reason=reason[:255],
        changed_at=timezone.now(),
    )
    log_audit_event(
        action=AuditLog.Action.BADGE_STATUS_CHANGED,
        outcome=AuditLog.Outcome.SUCCESS,
        actor=actor,
        school=locked_badge.student.school,
        target_type="badge",
        target_id=locked_badge.public_id,
        ip_address=ip_address,
        details=f"{previous_status} -> {new_status}",
    )
    return locked_badge


@transaction.atomic
def create_activation_link(
    badge: Badge,
    *,
    actor: User | None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> tuple[BadgeActivationLink, str]:
    """Create a one-time activation link for an active badge, storing only its SHA-256 hash."""
    if badge.status != Badge.Status.ACTIVE:
        raise BadgeTransitionError(
            "Un lien d'activation ne peut être généré que pour un badge actif."
        )

    current_time = now or timezone.now()
    raw_token = secrets.token_urlsafe(32)
    link = BadgeActivationLink.objects.create(
        badge=badge,
        token_hash=hash_activation_token(raw_token),
        created_by=actor,
        created_at=current_time,
        expires_at=current_time + settings.PASSAGE_ACTIVATION_LINK_DURATION,
    )
    log_audit_event(
        action=AuditLog.Action.ACTIVATION_LINK_CREATED,
        outcome=AuditLog.Outcome.SUCCESS,
        actor=actor,
        school=badge.student.school,
        target_type="badge",
        target_id=badge.public_id,
        ip_address=ip_address,
        details="Lien d'activation généré (usage unique).",
    )
    return link, raw_token


def inspect_activation_link(
    raw_token: str,
    *,
    now: datetime | None = None,
) -> BadgeActivationLink:
    """Validate an activation link without consuming it (safe for GET previews)."""
    cleaned = (raw_token or "").strip()
    if not cleaned:
        raise ActivationLinkError("not_found", "Lien d'activation introuvable.")

    link = (
        BadgeActivationLink.objects.select_related("badge")
        .filter(token_hash=hash_activation_token(cleaned))
        .first()
    )
    if link is None:
        raise ActivationLinkError("not_found", "Lien d'activation introuvable.")

    current_time = now or timezone.now()
    if link.used_at is not None:
        raise ActivationLinkError("already_used", "Ce lien d'activation a déjà été utilisé.")
    if current_time >= link.expires_at:
        raise ActivationLinkError("expired", "Ce lien d'activation a expiré.")
    if link.badge.status != Badge.Status.ACTIVE:
        raise ActivationLinkError("badge_inactive", "Ce badge n'est plus actif.")

    return link


@transaction.atomic
def consume_activation_link(
    raw_token: str,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Consume a one-time activation link atomically and deliver the badge secret ONCE (BAD-07)."""
    cleaned = (raw_token or "").strip()
    if not cleaned:
        raise ActivationLinkError("not_found", "Lien d'activation introuvable.")

    link = (
        BadgeActivationLink.objects.select_for_update()
        .select_related("badge")
        .filter(token_hash=hash_activation_token(cleaned))
        .first()
    )
    if link is None:
        raise ActivationLinkError("not_found", "Lien d'activation introuvable.")

    current_time = now or timezone.now()
    if link.used_at is not None:
        raise ActivationLinkError("already_used", "Ce lien d'activation a déjà été utilisé.")
    if current_time >= link.expires_at:
        raise ActivationLinkError("expired", "Ce lien d'activation a expiré.")

    badge = link.badge
    if badge.status != Badge.Status.ACTIVE:
        raise ActivationLinkError("badge_inactive", "Ce badge n'est plus actif.")

    link.used_at = current_time
    link.save(update_fields=["used_at"])

    badge.is_device_activated = True
    badge.device_activated_at = current_time
    badge.save(update_fields=["is_device_activated", "device_activated_at"])

    # Deliver strictly opaque badge material (no student name or ID — BAD-01, SEC-10).
    return {
        "public_id": badge.public_id,
        "secret": badge.secret,
        "code_step_seconds": settings.PASSAGE_CODE_STEP_SECONDS,
        "server_time": current_time.isoformat(),
    }
