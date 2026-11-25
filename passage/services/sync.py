"""Idempotent batch synchronization service for offline portal scans (SCN-04..11, HOR, BAD)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from passage.models import Badge, Checkpoint, ClockOffset, Passage, User
from passage.repositories.badge_repo import (
    get_badge_by_public_id,
    get_passage_by_device_event,
)
from passage.services.anomalies import (
    evaluate_valid_passage,
    record_inactive_badge_anomaly,
)
from passage.services.badges import (
    is_time_step_fresh,
    parse_badge_code,
    verify_badge_signature,
)
from passage.services.clock import compute_clock_offset, resolve_passage_timestamps

MAX_EVENTS_PER_BATCH = 500


class SyncPayloadError(ValueError):
    """Raised when the outer synchronization payload is malformed."""


@dataclass(frozen=True)
class SyncItemResult:
    """Per-event acknowledgement returned to the scanner device (SCN-07)."""

    client_event_id: str
    status: str  # "accepted" | "duplicate" | "rejected"
    reason: str = ""
    passage_id: int | None = None
    retained_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "client_event_id": self.client_event_id,
            "status": self.status,
            "reason": self.reason,
        }
        if self.passage_id is not None:
            payload["passage_id"] = self.passage_id
        if self.retained_at:
            payload["retained_at"] = self.retained_at
        return payload


def _parse_aware_datetime(raw_value: Any, field_name: str) -> datetime:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise SyncPayloadError(f"Champ horaire manquant ou invalide : {field_name}.")
    parsed = parse_datetime(raw_value.strip())
    if parsed is None:
        raise SyncPayloadError(f"Format ISO-8601 invalide pour {field_name}.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def _record_rejected_passage(
    *,
    school,
    checkpoint: Checkpoint,
    agent: User,
    device_id: str,
    client_event_id: str,
    clock_eval,
    rejection_reason: str,
    badge: Badge | None = None,
    badge_public_id: str = "",
    time_step: int | None = None,
) -> Passage:
    return Passage.objects.create(
        school=school,
        badge=badge,
        student=badge.student if badge is not None else None,
        checkpoint=checkpoint,
        agent=agent,
        badge_public_id=badge_public_id[:64],
        time_step=time_step,
        device_id=device_id,
        client_event_id=client_event_id,
        local_scanned_at=clock_eval.local_scanned_at,
        server_received_at=clock_eval.server_received_at,
        corrected_at=clock_eval.corrected_at,
        retained_at=clock_eval.retained_at,
        clock_offset_seconds=clock_eval.clock_offset_seconds,
        clock_suspect=clock_eval.clock_suspect,
        status=Passage.Status.REJECTED,
        rejection_reason=rejection_reason,
        sync_status=Passage.SyncStatus.SYNCED,
    )


def _process_single_event(
    *,
    raw_event: Any,
    agent: User,
    device_id: str,
    server_time: datetime,
    offset_seconds: int,
    clock_suspect: bool,
    checkpoints_by_id: dict[int, Checkpoint],
) -> SyncItemResult:
    if not isinstance(raw_event, dict):
        return SyncItemResult(
            client_event_id="",
            status="rejected",
            reason="invalid_payload",
        )

    client_event_id = str(raw_event.get("client_event_id") or "").strip()
    if not client_event_id or len(client_event_id) > 64:
        return SyncItemResult(
            client_event_id=client_event_id[:64],
            status="rejected",
            reason="invalid_event_id",
        )

    # 1. Idempotency check before any work (SCN-08).
    existing = get_passage_by_device_event(device_id, client_event_id)
    if existing is not None:
        return SyncItemResult(
            client_event_id=client_event_id,
            status="duplicate",
            reason=existing.rejection_reason,
            passage_id=existing.id,
            retained_at=existing.retained_at.isoformat(),
        )

    # 2. Validate checkpoint ownership (GES-05) and scan timestamp.
    try:
        checkpoint_id = int(raw_event.get("checkpoint_id"))
    except (TypeError, ValueError):
        return SyncItemResult(
            client_event_id=client_event_id,
            status="rejected",
            reason="invalid_checkpoint",
        )

    checkpoint = checkpoints_by_id.get(checkpoint_id)
    if checkpoint is None:
        return SyncItemResult(
            client_event_id=client_event_id,
            status="rejected",
            reason="invalid_checkpoint",
        )

    try:
        local_scanned_at = _parse_aware_datetime(
            raw_event.get("local_scanned_at"), "local_scanned_at"
        )
    except SyncPayloadError:
        return SyncItemResult(
            client_event_id=client_event_id,
            status="rejected",
            reason="invalid_timestamp",
        )

    clock_eval = resolve_passage_timestamps(
        local_scanned_at=local_scanned_at,
        server_received_at=server_time,
        offset_seconds=offset_seconds,
        clock_suspect=clock_suspect,
    )

    raw_code = raw_event.get("code")
    parsed = parse_badge_code(raw_code) if isinstance(raw_code, str) else None
    school = agent.school

    try:
        with transaction.atomic():
            # 3. Syntactic validation (SCN-10).
            if parsed is None:
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.MALFORMED_CODE,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.MALFORMED_CODE,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            # 4. Badge lookup and school isolation (GES-05, SCN-10).
            badge = get_badge_by_public_id(parsed.public_id)
            if badge is None:
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.UNKNOWN_BADGE,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.UNKNOWN_BADGE,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            if badge.student.school_id != school.id:
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.SCHOOL_MISMATCH,
                    badge=None,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.SCHOOL_MISMATCH,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            # 5. Cryptographic signature check (BAD-02, SEC-12).
            if not verify_badge_signature(badge=badge, parsed=parsed):
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.INVALID_SIGNATURE,
                    badge=badge,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.INVALID_SIGNATURE,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            # 6. Freshness check against retained_at +/- TOLERANCE_PAS (BAD-04).
            if not is_time_step_fresh(
                code_time_step=parsed.time_step,
                retained_at=clock_eval.retained_at,
            ):
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.EXPIRED_CODE,
                    badge=badge,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.EXPIRED_CODE,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            # 7. Inactive badge check (BAD-09): rejected passage + BADGE_NON_ACTIF anomaly.
            if badge.status != Badge.Status.ACTIVE:
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.INACTIVE_BADGE,
                    badge=badge,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                record_inactive_badge_anomaly(passage=passage, badge=badge)
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.INACTIVE_BADGE,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            # 8. Single-use enforcement (BAD-03) via DB partial unique constraint.
            already_used = Passage.objects.filter(
                badge=badge,
                time_step=parsed.time_step,
                status=Passage.Status.VALID,
            ).exists()
            if already_used:
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.ALREADY_USED,
                    badge=badge,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.ALREADY_USED,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            try:
                with transaction.atomic():
                    passage = Passage.objects.create(
                        school=school,
                        badge=badge,
                        student=badge.student,
                        checkpoint=checkpoint,
                        agent=agent,
                        badge_public_id=parsed.public_id,
                        time_step=parsed.time_step,
                        device_id=device_id,
                        client_event_id=client_event_id,
                        local_scanned_at=clock_eval.local_scanned_at,
                        server_received_at=clock_eval.server_received_at,
                        corrected_at=clock_eval.corrected_at,
                        retained_at=clock_eval.retained_at,
                        clock_offset_seconds=clock_eval.clock_offset_seconds,
                        clock_suspect=clock_eval.clock_suspect,
                        status=Passage.Status.VALID,
                        rejection_reason=Passage.RejectionReason.NONE,
                        sync_status=Passage.SyncStatus.SYNCED,
                    )
            except IntegrityError:
                # Concurrent race on either (device_id, client_event_id) or (badge, time_step).
                raced = get_passage_by_device_event(device_id, client_event_id)
                if raced is not None:
                    return SyncItemResult(
                        client_event_id=client_event_id,
                        status="duplicate",
                        reason=raced.rejection_reason,
                        passage_id=raced.id,
                        retained_at=raced.retained_at.isoformat(),
                    )
                passage = _record_rejected_passage(
                    school=school,
                    checkpoint=checkpoint,
                    agent=agent,
                    device_id=device_id,
                    client_event_id=client_event_id,
                    clock_eval=clock_eval,
                    rejection_reason=Passage.RejectionReason.ALREADY_USED,
                    badge=badge,
                    badge_public_id=parsed.public_id,
                    time_step=parsed.time_step,
                )
                return SyncItemResult(
                    client_event_id=client_event_id,
                    status="rejected",
                    reason=Passage.RejectionReason.ALREADY_USED,
                    passage_id=passage.id,
                    retained_at=passage.retained_at.isoformat(),
                )

            evaluate_valid_passage(passage, reference_time=server_time)

            return SyncItemResult(
                client_event_id=client_event_id,
                status="accepted",
                reason="",
                passage_id=passage.id,
                retained_at=passage.retained_at.isoformat(),
            )
    except IntegrityError:
        # Concurrent race on (device_id, client_event_id) for rejected inserts.
        raced = get_passage_by_device_event(device_id, client_event_id)
        if raced is not None:
            return SyncItemResult(
                client_event_id=client_event_id,
                status="duplicate",
                reason=raced.rejection_reason,
                passage_id=raced.id,
                retained_at=raced.retained_at.isoformat(),
            )
        raise


def sync_scan_batch(
    *,
    agent: User,
    payload: dict[str, Any],
    server_time: datetime | None = None,
) -> dict[str, Any]:
    """Process an offline scan batch idempotently with per-item acknowledgements."""
    if not isinstance(payload, dict):
        raise SyncPayloadError("Corps JSON invalide.")

    device_id = str(payload.get("device_id") or "").strip()
    if not device_id or len(device_id) > 64:
        raise SyncPayloadError("Identifiant d'appareil manquant ou invalide.")

    device_time = _parse_aware_datetime(payload.get("device_time"), "device_time")

    events = payload.get("events")
    if not isinstance(events, list):
        raise SyncPayloadError("La liste 'events' est obligatoire.")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise SyncPayloadError(f"Un lot ne peut pas dépasser {MAX_EVENTS_PER_BATCH} scans.")

    now = server_time or timezone.now()
    offset_seconds, clock_suspect = compute_clock_offset(
        server_time=now,
        device_time=device_time,
    )

    ClockOffset.objects.create(
        device_id=device_id,
        agent=agent,
        synced_at=now,
        device_time=device_time,
        offset_seconds=offset_seconds,
        is_suspect=clock_suspect,
    )

    checkpoints_by_id = {
        cp.id: cp
        for cp in Checkpoint.objects.filter(school_id=agent.school_id, is_active=True)
    }

    results = [
        _process_single_event(
            raw_event=raw_event,
            agent=agent,
            device_id=device_id,
            server_time=now,
            offset_seconds=offset_seconds,
            clock_suspect=clock_suspect,
            checkpoints_by_id=checkpoints_by_id,
        ).to_dict()
        for raw_event in events
    ]

    return {
        "server_time": now.isoformat(),
        "clock_offset_seconds": offset_seconds,
        "clock_suspect": clock_suspect,
        "results": results,
    }
