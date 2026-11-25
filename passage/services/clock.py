"""Device clock-offset calculation and timestamp plausibility logic (HOR-01..05)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

MAX_FUTURE_TOLERANCE = timedelta(minutes=2)


@dataclass(frozen=True)
class ClockEvaluation:
    """Resolved timestamps for one scan event (local time is never mutated)."""

    local_scanned_at: datetime
    server_received_at: datetime
    corrected_at: datetime
    retained_at: datetime
    clock_offset_seconds: int
    clock_suspect: bool
    used_server_fallback: bool


def compute_clock_offset(
    *,
    server_time: datetime,
    device_time: datetime,
) -> tuple[int, bool]:
    """Compute signed offset (server - device) in seconds and whether clock is suspect."""
    offset_seconds = int(round((server_time - device_time).total_seconds()))
    threshold_seconds = int(settings.PASSAGE_CLOCK_SKEW_THRESHOLD.total_seconds())
    is_suspect = abs(offset_seconds) > threshold_seconds
    return offset_seconds, is_suspect


def resolve_passage_timestamps(
    *,
    local_scanned_at: datetime,
    server_received_at: datetime,
    offset_seconds: int,
    clock_suspect: bool,
) -> ClockEvaluation:
    """Calculate corrected_at and retained_at without ever rejecting a passage for clock error."""
    corrected_at = local_scanned_at + timedelta(seconds=offset_seconds)
    latest_plausible = server_received_at + MAX_FUTURE_TOLERANCE
    earliest_plausible = server_received_at - settings.PASSAGE_MAX_AGE

    is_plausible = earliest_plausible <= corrected_at <= latest_plausible
    retained_at = corrected_at if is_plausible else server_received_at
    return ClockEvaluation(
        local_scanned_at=local_scanned_at,
        server_received_at=server_received_at,
        corrected_at=corrected_at,
        retained_at=retained_at,
        clock_offset_seconds=offset_seconds,
        clock_suspect=clock_suspect or not is_plausible,
        used_server_fallback=not is_plausible,
    )
