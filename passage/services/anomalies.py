"""Automated anomaly detection, late-sync auto-resolution, and manager handling (ITI-01..03, ANO-01..07)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from passage.models import (
    Anomaly,
    Badge,
    Checkpoint,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)


@dataclass(frozen=True)
class DetectionCycleResult:
    """Summary counters returned by a single scheduler detection pass."""

    checked_windows: int
    missing_created: int
    missing_resolved: int


def _get_grace_delay() -> timedelta:
    raw = getattr(settings, "PASSAGE_GRACE_DELAY", timedelta(minutes=10))
    if isinstance(raw, timedelta):
        return raw
    return timedelta(minutes=int(raw))


def _get_double_scan_interval() -> timedelta:
    raw = getattr(settings, "PASSAGE_DOUBLE_SCAN_INTERVAL_SECONDS", 60)
    if isinstance(raw, timedelta):
        return raw
    return timedelta(seconds=int(raw))


def _school_tz(school: School) -> ZoneInfo:
    return ZoneInfo(school.timezone)


def _format_local_time(dt: datetime) -> str:
    return f"{dt:%H} h {dt:%M}"


def _window_bounds(
    window: RouteWindow,
    local_date: date,
    school_tz: ZoneInfo,
) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(local_date, window.start_time, tzinfo=school_tz)
    end_dt = datetime.combine(local_date, window.end_time, tzinfo=school_tz)
    return start_dt, end_dt


def _is_within_window(
    local_dt: datetime,
    window: RouteWindow,
    local_date: date,
    school_tz: ZoneInfo,
) -> bool:
    start_dt, end_dt = _window_bounds(window, local_date, school_tz)
    return start_dt <= local_dt <= end_dt


def _has_valid_passage_in_window(
    *,
    student: Student,
    checkpoint: Checkpoint,
    window: RouteWindow,
    local_date: date,
    school_tz: ZoneInfo,
) -> bool:
    start_dt, end_dt = _window_bounds(window, local_date, school_tz)
    return Passage.objects.filter(
        student=student,
        checkpoint=checkpoint,
        status=Passage.Status.VALID,
        retained_at__gte=start_dt,
        retained_at__lte=end_dt,
    ).exists()


def get_applicable_windows_for_student(
    student: Student,
    local_date: date,
) -> list[RouteWindow]:
    """Return active route windows applying to `student` on `local_date` (ITI-01..03)."""
    if student.status != Student.Status.ACTIVE:
        return []
    iso_weekday = local_date.isoweekday()
    windows = (
        RouteWindow.objects.filter(
            student=student,
            is_active=True,
            checkpoint__is_active=True,
        )
        .select_related("checkpoint")
        .order_by("order", "start_time")
    )
    return [w for w in windows if w.applies_to_weekday(iso_weekday)]


def record_inactive_badge_anomaly(
    *,
    passage: Passage,
    badge: Badge,
) -> Anomaly:
    """Create a BADGE_NON_ACTIF anomaly for a validly-signed inactive badge scan (BAD-09, ANO-01)."""
    existing = Anomaly.objects.filter(
        passage=passage,
        anomaly_type=Anomaly.AnomalyType.BADGE_NON_ACTIF,
    ).first()
    if existing is not None:
        return existing

    school_tz = _school_tz(passage.school)
    local_date = passage.retained_at.astimezone(school_tz).date()
    return Anomaly.objects.create(
        school=passage.school,
        student=badge.student,
        checkpoint=passage.checkpoint,
        passage=passage,
        anomaly_type=Anomaly.AnomalyType.BADGE_NON_ACTIF,
        date=local_date,
        status=Anomaly.Status.OPEN,
        details=f"Tentative d'utilisation d'un badge {badge.get_status_display().lower()}.",
    )


def _auto_resolve_missing_point_if_satisfied(
    *,
    student: Student,
    checkpoint: Checkpoint,
    local_date: date,
    checkpoint_windows: list[RouteWindow],
    school_tz: ZoneInfo,
    reference_now: datetime,
    resolving_passage: Passage | None = None,
) -> bool:
    """Resolve an open POINT_MANQUANT when all closed windows on that checkpoint are now satisfied (ANO-04)."""
    open_missing = Anomaly.objects.filter(
        student=student,
        date=local_date,
        checkpoint=checkpoint,
        anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
        status=Anomaly.Status.OPEN,
    ).first()
    if open_missing is None:
        return False

    grace_delay = _get_grace_delay()
    now_local = reference_now.astimezone(school_tz)

    closed_windows = [
        w
        for w in checkpoint_windows
        if _window_bounds(w, local_date, school_tz)[1] + grace_delay <= now_local
    ]
    windows_to_verify = closed_windows or checkpoint_windows
    if not all(
        _has_valid_passage_in_window(
            student=student,
            checkpoint=checkpoint,
            window=w,
            local_date=local_date,
            school_tz=school_tz,
        )
        for w in windows_to_verify
    ):
        return False

    open_missing.status = Anomaly.Status.AUTO_RESOLVED
    open_missing.resolved_at = reference_now
    if resolving_passage is not None and open_missing.passage_id is None:
        open_missing.passage = resolving_passage
    open_missing.details = (
        f"Point manquant au {checkpoint.label} résolu automatiquement "
        "par un passage valide synchronisé tardivement."
    )[:255]
    open_missing.save(update_fields=["status", "resolved_at", "passage", "details"])
    return True


def _detect_double_scan_for_passage(
    *,
    passage: Passage,
    local_date: date,
) -> Anomaly | None:
    """Detect DOUBLE_SCAN strictly under INTERVALLE_DOUBLE_SCAN seconds with 1 anomaly per pair (ANO-07)."""
    if passage.badge_id is None:
        return None

    interval = _get_double_scan_interval()
    nearby_passages = list(
        Passage.objects.filter(
            badge_id=passage.badge_id,
            status=Passage.Status.VALID,
            retained_at__gt=passage.retained_at - interval,
            retained_at__lt=passage.retained_at + interval,
        )
        .exclude(pk=passage.pk)
        .order_by("retained_at", "pk")
    )
    if not nearby_passages:
        return None

    # Find the closest preceding passage (or closest following passage if synced out of order).
    preceding = [
        p
        for p in nearby_passages
        if (p.retained_at, p.pk) < (passage.retained_at, passage.pk)
    ]
    if preceding:
        partner = preceding[-1]
        anchor_passage = passage
    else:
        partner = nearby_passages[0]
        anchor_passage = partner

    # ANO-07: at most one DOUBLE_SCAN anomaly per pair, attached to the second passage of the pair.
    if Anomaly.objects.filter(
        passage=anchor_passage,
        anomaly_type=Anomaly.AnomalyType.DOUBLE_SCAN,
    ).exists():
        return None

    gap_seconds = int(round(abs((anchor_passage.retained_at - partner.retained_at).total_seconds())))
    anchor_date = anchor_passage.retained_at.astimezone(_school_tz(anchor_passage.school)).date()
    return Anomaly.objects.create(
        school=anchor_passage.school,
        student=anchor_passage.student,
        checkpoint=anchor_passage.checkpoint,
        passage=anchor_passage,
        anomaly_type=Anomaly.AnomalyType.DOUBLE_SCAN,
        date=anchor_date,
        status=Anomaly.Status.OPEN,
        details=f"Deux passages valides enregistrés à {gap_seconds} seconde(s) d'intervalle.",
    )


@transaction.atomic
def evaluate_valid_passage(
    passage: Passage,
    *,
    reference_time: datetime | None = None,
) -> list[Anomaly]:
    """Evaluate a valid passage against itineraries and detect passage-level anomalies (ITI-01..03, ANO-01..07)."""
    if passage.status != Passage.Status.VALID or passage.student_id is None:
        return []

    now = reference_time or timezone.now()
    school_tz = _school_tz(passage.school)
    local_dt = passage.retained_at.astimezone(school_tz)
    local_date = local_dt.date()

    # ITI-02: no applicable window on this day -> no passage anomaly detection for this student today.
    applicable_windows = get_applicable_windows_for_student(passage.student, local_date)
    if not applicable_windows:
        return []

    created: list[Anomaly] = []
    windows_for_checkpoint = [
        w for w in applicable_windows if w.checkpoint_id == passage.checkpoint_id
    ]

    if not windows_for_checkpoint:
        # POINT_INCONNU: checkpoint absent from the student's applicable itinerary.
        if not Anomaly.objects.filter(
            passage=passage,
            anomaly_type=Anomaly.AnomalyType.POINT_INCONNU,
        ).exists():
            created.append(
                Anomaly.objects.create(
                    school=passage.school,
                    student=passage.student,
                    checkpoint=passage.checkpoint,
                    passage=passage,
                    anomaly_type=Anomaly.AnomalyType.POINT_INCONNU,
                    date=local_date,
                    status=Anomaly.Status.OPEN,
                    details=(
                        f"Passage au point {passage.checkpoint.label} absent "
                        "de l'itinéraire prévu ce jour."
                    )[:255],
                )
            )
    else:
        in_any_window = any(
            _is_within_window(local_dt, w, local_date, school_tz)
            for w in windows_for_checkpoint
        )
        if in_any_window:
            # ANO-04: late-arriving valid passage inside the expected window resolves open POINT_MANQUANT.
            _auto_resolve_missing_point_if_satisfied(
                student=passage.student,
                checkpoint=passage.checkpoint,
                local_date=local_date,
                checkpoint_windows=windows_for_checkpoint,
                school_tz=school_tz,
                reference_now=now,
                resolving_passage=passage,
            )
        else:
            # HORS_FENETRE: valid passage on an itinerary checkpoint outside every applicable window.
            if not Anomaly.objects.filter(
                passage=passage,
                anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
            ).exists():
                window_summary = ", ".join(
                    f"{w.start_time:%H} h {w.start_time:%M} à {w.end_time:%H} h {w.end_time:%M}"
                    for w in windows_for_checkpoint
                )
                created.append(
                    Anomaly.objects.create(
                        school=passage.school,
                        student=passage.student,
                        checkpoint=passage.checkpoint,
                        passage=passage,
                        anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
                        date=local_date,
                        status=Anomaly.Status.OPEN,
                        details=(
                            f"Passage au {passage.checkpoint.label} à {_format_local_time(local_dt)} "
                            f"en dehors de la fenêtre prévue ({window_summary})."
                        )[:255],
                    )
                )

    double_scan = _detect_double_scan_for_passage(
        passage=passage,
        local_date=local_date,
    )
    if double_scan is not None:
        created.append(double_scan)

    return created


def detect_missing_points(
    *,
    reference_time: datetime | None = None,
    school: School | None = None,
) -> DetectionCycleResult:
    """Idempotently detect POINT_MANQUANT anomalies for closed windows + DELAI_GRACE (ANO-02, ANO-03)."""
    now = reference_time or timezone.now()
    grace_delay = _get_grace_delay()

    schools = (
        [school]
        if school is not None
        else list(School.objects.filter(is_active=True))
    )

    checked_windows = 0
    missing_created = 0
    missing_resolved = 0

    for current_school in schools:
        school_tz = _school_tz(current_school)
        now_local = now.astimezone(school_tz)
        local_date = now_local.date()
        iso_weekday = local_date.isoweekday()

        windows = (
            RouteWindow.objects.filter(
                is_active=True,
                student__school=current_school,
                student__status=Student.Status.ACTIVE,
                checkpoint__is_active=True,
            )
            .select_related("student", "checkpoint")
            .order_by("student_id", "order", "start_time")
        )

        grouped: dict[tuple[int, int], list[RouteWindow]] = defaultdict(list)
        for window in windows:
            if window.applies_to_weekday(iso_weekday):
                grouped[(window.student_id, window.checkpoint_id)].append(window)

        for cp_windows in grouped.values():
            student = cp_windows[0].student
            checkpoint = cp_windows[0].checkpoint

            # Reconcile any existing open POINT_MANQUANT that now has a valid in-window passage.
            if _auto_resolve_missing_point_if_satisfied(
                student=student,
                checkpoint=checkpoint,
                local_date=local_date,
                checkpoint_windows=cp_windows,
                school_tz=school_tz,
                reference_now=now,
            ):
                missing_resolved += 1

            for window in cp_windows:
                checked_windows += 1
                start_dt, end_dt = _window_bounds(window, local_date, school_tz)
                deadline = end_dt + grace_delay
                if now_local < deadline:
                    continue

                if _has_valid_passage_in_window(
                    student=student,
                    checkpoint=checkpoint,
                    window=window,
                    local_date=local_date,
                    school_tz=school_tz,
                ):
                    continue

                if Anomaly.objects.filter(
                    student=student,
                    date=local_date,
                    checkpoint=checkpoint,
                    anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
                ).exists():
                    continue

                try:
                    with transaction.atomic():
                        _, created = Anomaly.objects.get_or_create(
                            student=student,
                            date=local_date,
                            checkpoint=checkpoint,
                            anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
                            defaults={
                                "school": current_school,
                                "passage": None,
                                "status": Anomaly.Status.OPEN,
                                "detected_at": now,
                                "details": (
                                    f"Aucun passage valide au {checkpoint.label} sur la fenêtre "
                                    f"{start_dt:%H} h {start_dt:%M} à {end_dt:%H} h {end_dt:%M}."
                                )[:255],
                            },
                        )
                    if created:
                        missing_created += 1
                except IntegrityError:
                    # Concurrent scheduler/web insert hit uniq_missing_point_anomaly: idempotent.
                    pass

    return DetectionCycleResult(
        checked_windows=checked_windows,
        missing_created=missing_created,
        missing_resolved=missing_resolved,
    )


@transaction.atomic
def mark_anomaly_handled(
    anomaly: Anomaly,
    *,
    actor: User,
) -> Anomaly:
    """Mark an anomaly as handled by a school manager (ANO-05)."""
    locked = Anomaly.objects.select_for_update().get(pk=anomaly.pk)
    if locked.status != Anomaly.Status.HANDLED:
        locked.status = Anomaly.Status.HANDLED
        locked.handled_by = actor
        if locked.resolved_at is None:
            locked.resolved_at = timezone.now()
        locked.save(update_fields=["status", "handled_by", "resolved_at"])
    return locked
