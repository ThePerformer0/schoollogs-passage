"""AI-assisted daily classroom summaries with strict guardrails and deterministic fallback (SYN-01..12)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from passage.models import (
    Anomaly,
    AuditLog,
    Checkpoint,
    DailySummary,
    ModelCallLog,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)
from passage.services.audit import log_audit_event
from passage.services.gateways.llm_gateway import (
    LLMGateway,
    LLMServiceError,
    LLMTimeoutError,
    get_llm_gateway,
)

MIN_SUMMARY_LENGTH = 10
MAX_SUMMARY_LENGTH = 500
TIME_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:[hH]|:)\s*(\d{2})\b")
NUMBER_PATTERN = re.compile(r"\d+")
DECIMAL_OR_NEGATIVE_PATTERN = re.compile(r"(?:^|[^\w])-\d+|\d+[.,]\d+")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

SYSTEM_INSTRUCTION = (
    "Tu es un assistant scolaire neutre et factuel. "
    "Rédige en français une synthèse concise (1 à 2 phrases, maximum 400 caractères) "
    "de la journée d'une classe uniquement à partir des chiffres agrégés fournis. "
    "Règles strictes : "
    "1) Réponds uniquement avec un objet JSON valide de la forme {\"summary\": \"...\"}. "
    "2) N'invente, ne calcule et ne modifie aucun nombre : chaque nombre écrit dans le texte "
    "doit provenir exactement des chiffres transmis. "
    "3) Mentionne tous les indicateurs non nuls et toutes les heures de sortie hors plage transmises "
    "au format exact « HH h MM ». "
    "4) Ne cite aucun nom d'élève ni identifiant. "
    "5) Ne mentionne ni la date ni l'année dans la phrase (elles figurent déjà dans l'en-tête)."
)


def _get_api_key() -> str:
    return (getattr(settings, "PASSAGE_AI_API_KEY", "") or "").strip()


def _get_model_name() -> str:
    return (getattr(settings, "PASSAGE_AI_MODEL_NAME", "gemini-2.5-flash") or "gemini-2.5-flash").strip()


def _get_prompt_version() -> str:
    return (getattr(settings, "PASSAGE_AI_PROMPT_VERSION", "v1") or "v1").strip()


def _get_timeout_seconds() -> int:
    return max(1, int(getattr(settings, "PASSAGE_AI_TIMEOUT_SECONDS", 10)))


def _get_daily_call_limit() -> int:
    return max(0, int(getattr(settings, "PASSAGE_AI_DAILY_CALL_LIMIT", 20)))


def _get_daily_cost_limit() -> Decimal:
    raw = getattr(settings, "PASSAGE_AI_DAILY_COST_LIMIT_USD", Decimal("0.50"))
    return Decimal(str(raw))


def estimate_call_cost_usd(*, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate USD cost using configurable per-1M token pricing even on free tiers (SYN-07)."""
    in_rate = Decimal(str(getattr(settings, "PASSAGE_AI_INPUT_COST_PER_1M_USD", Decimal("0.15"))))
    out_rate = Decimal(str(getattr(settings, "PASSAGE_AI_OUTPUT_COST_PER_1M_USD", Decimal("0.60"))))
    million = Decimal("1000000")
    cost = (Decimal(max(0, input_tokens)) * in_rate + Decimal(max(0, output_tokens)) * out_rate) / million
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def compute_classroom_metrics(
    *,
    school: School,
    classroom: str,
    target_date: date,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Compute all daily class metrics strictly in Python from DB, with zero student names (SYN-02, SYN-03)."""
    now = reference_time or timezone.now()
    school_tz = ZoneInfo(school.timezone)
    now_local = now.astimezone(school_tz)
    iso_weekday = target_date.isoweekday()

    day_start = datetime.combine(target_date, time.min, tzinfo=school_tz)
    day_end = day_start + timedelta(days=1)

    students = list(
        Student.objects.filter(
            school=school,
            classroom=classroom,
            status=Student.Status.ACTIVE,
        ).only("id")
    )
    student_ids = [s.id for s in students]
    total_students = len(student_ids)

    if not student_ids:
        return {
            "classroom": classroom,
            "date": target_date.isoformat(),
            "total_students": 0,
            "present_count": 0,
            "absent_count": 0,
            "late_entry_count": 0,
            "off_window_exit_count": 0,
            "off_window_exit_times": [],
        }

    # 1. Present (SYN-03): >= 1 valid passage at an ENTRY_GATE checkpoint on target_date.
    present_student_ids = set(
        Passage.objects.filter(
            school=school,
            student_id__in=student_ids,
            status=Passage.Status.VALID,
            checkpoint__checkpoint_type=Checkpoint.CheckpointType.ENTRY_GATE,
            retained_at__gte=day_start,
            retained_at__lt=day_end,
        ).values_list("student_id", flat=True).distinct()
    )
    present_count = len(present_student_ids)

    # Fetch applicable ENTRY_GATE windows on target_date to evaluate lateness and closure.
    entry_windows = [
        w
        for w in RouteWindow.objects.filter(
            student_id__in=student_ids,
            is_active=True,
            checkpoint__is_active=True,
            checkpoint__checkpoint_type=Checkpoint.CheckpointType.ENTRY_GATE,
        ).select_related("checkpoint")
        if w.applies_to_weekday(iso_weekday)
    ]

    windows_by_student_cp: dict[tuple[int, int], list[RouteWindow]] = {}
    expected_closed_student_ids: set[int] = set()
    for window in entry_windows:
        windows_by_student_cp.setdefault((window.student_id, window.checkpoint_id), []).append(window)
        window_end_dt = datetime.combine(target_date, window.end_time, tzinfo=school_tz)
        if now_local >= window_end_dt:
            expected_closed_student_ids.add(window.student_id)

    # Also include students with an explicit non-resolved POINT_MANQUANT at an entry gate.
    explicit_missing_student_ids = set(
        Anomaly.objects.filter(
            school=school,
            student_id__in=student_ids,
            date=target_date,
            anomaly_type=Anomaly.AnomalyType.POINT_MANQUANT,
            checkpoint__checkpoint_type=Checkpoint.CheckpointType.ENTRY_GATE,
        )
        .exclude(status=Anomaly.Status.AUTO_RESOLVED)
        .values_list("student_id", flat=True)
        .distinct()
    )

    # 2. Absent (SYN-03): expected at entry closure and without any valid entry passage that day.
    absent_student_ids = (expected_closed_student_ids | explicit_missing_student_ids) - present_student_ids
    absent_count = len(absent_student_ids)

    # 3. Late entry (SYN-03): HORS_FENETRE anomaly on ENTRY_GATE with retained_at > window end_time.
    entry_hors_fenetre = (
        Anomaly.objects.filter(
            school=school,
            student_id__in=student_ids,
            date=target_date,
            anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
            checkpoint__checkpoint_type=Checkpoint.CheckpointType.ENTRY_GATE,
        )
        .select_related("passage")
    )
    late_entry_count = 0
    for anomaly in entry_hors_fenetre:
        cp_windows = windows_by_student_cp.get((anomaly.student_id, anomaly.checkpoint_id), [])
        if anomaly.passage is not None and cp_windows:
            local_scan_time = anomaly.passage.retained_at.astimezone(school_tz).time()
            latest_end = max(w.end_time for w in cp_windows)
            if local_scan_time > latest_end:
                late_entry_count += 1
        elif anomaly.passage is None or not cp_windows:
            late_entry_count += 1

    # 4. Off-window exit (SYN-03): HORS_FENETRE anomaly on EXIT_GATE with local time.
    exit_hors_fenetre = (
        Anomaly.objects.filter(
            school=school,
            student_id__in=student_ids,
            date=target_date,
            anomaly_type=Anomaly.AnomalyType.HORS_FENETRE,
            checkpoint__checkpoint_type=Checkpoint.CheckpointType.EXIT_GATE,
        )
        .select_related("passage")
        .order_by("passage__retained_at", "detected_at")
    )
    off_window_exit_count = 0
    off_window_exit_times: list[str] = []
    for anomaly in exit_hors_fenetre:
        off_window_exit_count += 1
        if anomaly.passage is not None:
            local_exit = anomaly.passage.retained_at.astimezone(school_tz)
            off_window_exit_times.append(f"{local_exit:%H} h {local_exit:%M}")

    return {
        "classroom": classroom,
        "date": target_date.isoformat(),
        "total_students": total_students,
        "present_count": present_count,
        "absent_count": absent_count,
        "late_entry_count": late_entry_count,
        "off_window_exit_count": off_window_exit_count,
        "off_window_exit_times": off_window_exit_times,
    }


def render_template_summary(metrics: dict[str, Any]) -> str:
    """Produce a dry, exact, deterministic French summary directly from computed metrics (SYN-06)."""
    present = int(metrics["present_count"])
    absent = int(metrics["absent_count"])
    late = int(metrics["late_entry_count"])
    exits = int(metrics["off_window_exit_count"])
    exit_times: list[str] = list(metrics.get("off_window_exit_times") or [])

    parts: list[str] = []
    parts.append(f"{present} élève présent" if present <= 1 else f"{present} élèves présents")

    if absent == 0:
        parts.append("aucun absent")
    elif absent == 1:
        parts.append("1 absent")
    else:
        parts.append(f"{absent} absents")

    if late == 0:
        parts.append("aucun retard au portail")
    elif late == 1:
        parts.append("1 retard au portail")
    else:
        parts.append(f"{late} retards au portail")

    if exits == 0:
        parts.append("aucune sortie hors plage")
    else:
        label = "1 sortie hors plage" if exits == 1 else f"{exits} sorties hors plage"
        if exit_times:
            label = f"{label} à {', '.join(exit_times)}"
        parts.append(label)

    return ", ".join(parts) + "."


def _parse_normalized_times(values: list[str]) -> set[tuple[int, int]]:
    normalized: set[tuple[int, int]] = set()
    for val in values:
        for match in TIME_PATTERN.finditer(val):
            normalized.add((int(match.group(1)), int(match.group(2))))
    return normalized


def validate_summary_text(summary: str, metrics: dict[str, Any]) -> tuple[bool, str]:
    """Validate bounded length, absence of markup, and strict numerical fidelity (SYN-04, SYN-11)."""
    if not isinstance(summary, str):
        return False, "summary_not_string"

    cleaned = summary.strip()
    if len(cleaned) < MIN_SUMMARY_LENGTH:
        return False, "summary_too_short"
    if len(cleaned) > MAX_SUMMARY_LENGTH:
        return False, "summary_too_long"
    if HTML_TAG_PATTERN.search(cleaned):
        return False, "summary_contains_html"
    if DECIMAL_OR_NEGATIVE_PATTERN.search(cleaned):
        return False, "unexpected_number_in_summary"

    present = int(metrics["present_count"])
    absent = int(metrics["absent_count"])
    late = int(metrics["late_entry_count"])
    exits = int(metrics["off_window_exit_count"])
    total = int(metrics["total_students"])
    exit_times = list(metrics.get("off_window_exit_times") or [])

    expected_time_pairs = _parse_normalized_times(exit_times)
    found_time_pairs = _parse_normalized_times([cleaned])
    if not found_time_pairs.issubset(expected_time_pairs):
        return False, "unexpected_time_in_summary"
    if not expected_time_pairs.issubset(found_time_pairs):
        return False, "missing_exit_time_in_summary"

    allowed_numbers: set[int] = {present, absent, late, exits, total}
    for hour, minute in expected_time_pairs:
        allowed_numbers.add(hour)
        allowed_numbers.add(minute)

    found_numbers = {int(tok) for tok in NUMBER_PATTERN.findall(cleaned)}
    if not found_numbers.issubset(allowed_numbers):
        return False, "unexpected_number_in_summary"

    # Every non-zero metric count must appear explicitly in the summary.
    required_numbers = {val for val in (present, absent, late, exits) if val > 0}
    if not required_numbers.issubset(found_numbers):
        return False, "missing_required_metric_number"

    if present == 0 and 0 not in found_numbers and "aucun" not in cleaned.lower():
        return False, "missing_zero_presence"

    return True, ""


def parse_and_validate_llm_output(
    raw_text: str,
    metrics: dict[str, Any],
) -> tuple[str | None, str]:
    """Strictly parse JSON schema {\"summary\": \"...\"} and validate numerical fidelity (SYN-04)."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None, "empty_response"

    try:
        parsed = json.loads(raw_text.strip())
    except ValueError:
        return None, "invalid_json"

    if not isinstance(parsed, dict):
        return None, "schema_not_object"
    if "summary" not in parsed or not isinstance(parsed["summary"], str):
        return None, "missing_summary_field"

    cleaned = " ".join(parsed["summary"].split())
    ok, reason = validate_summary_text(cleaned, metrics)
    if not ok:
        return None, reason
    return cleaned, ""


def build_llm_metrics_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    """Build the strictly non-nominative aggregates payload sent to the LLM (SYN-02)."""
    return {
        "date": metrics["date"],
        "total_students": metrics["total_students"],
        "present_count": metrics["present_count"],
        "absent_count": metrics["absent_count"],
        "late_entry_count": metrics["late_entry_count"],
        "off_window_exit_count": metrics["off_window_exit_count"],
        "off_window_exit_times": list(metrics["off_window_exit_times"]),
    }


def compute_metrics_hash(
    *,
    metrics: dict[str, Any],
    prompt_version: str,
    model_name: str,
) -> str:
    """Compute a deterministic deduplication hash over metrics, prompt version, and model (SYN-09)."""
    canonical = json.dumps(
        {
            "metrics": metrics,
            "prompt_version": prompt_version,
            "model_name": model_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_daily_ai_usage(call_date: date) -> tuple[int, Decimal]:
    """Return (outbound_calls_count, total_estimated_cost_usd) recorded on `call_date`."""
    qs = ModelCallLog.objects.filter(
        call_date=call_date,
        result__in=[
            ModelCallLog.Result.SUCCESS,
            ModelCallLog.Result.INVALID_OUTPUT,
            ModelCallLog.Result.ERROR,
            ModelCallLog.Result.TIMEOUT,
        ],
    )
    count = qs.count()
    total_cost = qs.aggregate(total=Sum("estimated_cost_usd"))["total"] or Decimal("0")
    return count, Decimal(total_cost)


def is_daily_ai_limit_reached(call_date: date) -> bool:
    """Return True when daily call count or estimated USD cost ceiling is reached (SYN-08)."""
    calls, cost = get_daily_ai_usage(call_date)
    return calls >= _get_daily_call_limit() or cost >= _get_daily_cost_limit()


def _persist_summary(
    *,
    school: School,
    classroom: str,
    target_date: date,
    metrics: dict[str, Any],
    metrics_hash: str,
    prompt_version: str,
    model_name: str,
    summary_text: str,
    origin: str,
    generation_path: str,
    actor: User | None,
    ip_address: str | None,
    now: datetime,
) -> DailySummary:
    summary = DailySummary.objects.create(
        school=school,
        classroom=classroom,
        date=target_date,
        computed_metrics=metrics,
        metrics_hash=metrics_hash,
        prompt_version=prompt_version,
        model_name=model_name,
        summary_text=summary_text,
        origin=origin,
        generation_path=generation_path,
        generated_by=actor,
        created_at=now,
    )
    log_audit_event(
        action=AuditLog.Action.SUMMARY_GENERATED,
        outcome=AuditLog.Outcome.SUCCESS,
        actor=actor,
        school=school,
        target_type="daily_summary",
        target_id=summary.id,
        ip_address=ip_address,
        details=f"{classroom} ({target_date.isoformat()}) [{generation_path}]",
    )
    return summary


def generate_daily_summary(
    *,
    school: School,
    classroom: str,
    target_date: date,
    actor: User | None = None,
    ip_address: str | None = None,
    llm_gateway: LLMGateway | None = None,
    reference_time: datetime | None = None,
) -> DailySummary:
    """Generate and persist a daily classroom summary with all 4 AI guardrails (SYN-01..12)."""
    now = reference_time or timezone.now()
    school_tz = ZoneInfo(school.timezone)
    call_date = now.astimezone(school_tz).date()
    prompt_version = _get_prompt_version()

    metrics = compute_classroom_metrics(
        school=school,
        classroom=classroom,
        target_date=target_date,
        reference_time=now,
    )
    template_text = render_template_summary(metrics)

    api_key = _get_api_key()
    model_name = _get_model_name()

    # Guardrail 1: Degraded mode when no API key is configured (SYN-06).
    if not api_key and llm_gateway is None:
        metrics_hash = compute_metrics_hash(
            metrics=metrics,
            prompt_version=prompt_version,
            model_name="",
        )
        return _persist_summary(
            school=school,
            classroom=classroom,
            target_date=target_date,
            metrics=metrics,
            metrics_hash=metrics_hash,
            prompt_version=prompt_version,
            model_name="",
            summary_text=template_text,
            origin=DailySummary.Origin.TEMPLATE,
            generation_path=DailySummary.GenerationPath.GABARIT_SANS_CLE,
            actor=actor,
            ip_address=ip_address,
            now=now,
        )

    metrics_hash = compute_metrics_hash(
        metrics=metrics,
        prompt_version=prompt_version,
        model_name=model_name,
    )

    # Guardrail 2: Deduplication cache preventing double billing on identical inputs (SYN-09).
    cached = (
        DailySummary.objects.filter(
            school=school,
            classroom=classroom,
            date=target_date,
            metrics_hash=metrics_hash,
            prompt_version=prompt_version,
            model_name=model_name,
            origin=DailySummary.Origin.MODEL,
        )
        .order_by("-created_at")
        .first()
    )
    if cached is not None:
        return _persist_summary(
            school=school,
            classroom=classroom,
            target_date=target_date,
            metrics=metrics,
            metrics_hash=metrics_hash,
            prompt_version=prompt_version,
            model_name=model_name,
            summary_text=cached.summary_text,
            origin=cached.origin,
            generation_path=DailySummary.GenerationPath.CACHE,
            actor=actor,
            ip_address=ip_address,
            now=now,
        )

    # Guardrail 3: Daily call count and cost ceiling (SYN-08).
    if is_daily_ai_limit_reached(call_date):
        ModelCallLog.objects.create(
            school=school,
            called_at=now,
            call_date=call_date,
            model_name=model_name,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0"),
            result=ModelCallLog.Result.BLOCKED_LIMIT,
            error_code="daily_limit_reached",
        )
        return _persist_summary(
            school=school,
            classroom=classroom,
            target_date=target_date,
            metrics=metrics,
            metrics_hash=metrics_hash,
            prompt_version=prompt_version,
            model_name=model_name,
            summary_text=template_text,
            origin=DailySummary.Origin.TEMPLATE,
            generation_path=DailySummary.GenerationPath.GABARIT_PLAFOND_ATTEINT,
            actor=actor,
            ip_address=ip_address,
            now=now,
        )

    gateway = llm_gateway or get_llm_gateway(
        api_key=api_key,
        model_name=model_name,
        timeout_seconds=_get_timeout_seconds(),
    )
    llm_payload = build_llm_metrics_payload(metrics)

    # Guardrail 4: Constrained output + numerical fidelity check + at most 1 retry (SYN-04, SYN-05, SYN-07).
    retry_reason: str | None = None
    for attempt in (1, 2):
        if attempt == 2 and is_daily_ai_limit_reached(call_date):
            ModelCallLog.objects.create(
                school=school,
                called_at=now,
                call_date=call_date,
                model_name=model_name,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
                result=ModelCallLog.Result.BLOCKED_LIMIT,
                error_code="daily_limit_reached_before_retry",
            )
            return _persist_summary(
                school=school,
                classroom=classroom,
                target_date=target_date,
                metrics=metrics,
                metrics_hash=metrics_hash,
                prompt_version=prompt_version,
                model_name=model_name,
                summary_text=template_text,
                origin=DailySummary.Origin.TEMPLATE,
                generation_path=DailySummary.GenerationPath.GABARIT_PLAFOND_ATTEINT,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        try:
            response = gateway.generate_summary(
                system_instruction=SYSTEM_INSTRUCTION,
                metrics_payload=llm_payload,
                retry_reason=retry_reason,
            )
        except LLMTimeoutError as exc:
            ModelCallLog.objects.create(
                school=school,
                called_at=now,
                call_date=call_date,
                model_name=model_name,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
                result=ModelCallLog.Result.TIMEOUT,
                error_code=exc.error_code,
            )
            return _persist_summary(
                school=school,
                classroom=classroom,
                target_date=target_date,
                metrics=metrics,
                metrics_hash=metrics_hash,
                prompt_version=prompt_version,
                model_name=model_name,
                summary_text=template_text,
                origin=DailySummary.Origin.TEMPLATE,
                generation_path=DailySummary.GenerationPath.GABARIT_SERVICE_INDISPONIBLE,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )
        except LLMServiceError as exc:
            ModelCallLog.objects.create(
                school=school,
                called_at=now,
                call_date=call_date,
                model_name=model_name,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=Decimal("0"),
                result=ModelCallLog.Result.ERROR,
                error_code=exc.error_code,
            )
            return _persist_summary(
                school=school,
                classroom=classroom,
                target_date=target_date,
                metrics=metrics,
                metrics_hash=metrics_hash,
                prompt_version=prompt_version,
                model_name=model_name,
                summary_text=template_text,
                origin=DailySummary.Origin.TEMPLATE,
                generation_path=DailySummary.GenerationPath.GABARIT_SERVICE_INDISPONIBLE,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        used_model = (response.model_name or model_name)[:80]
        estimated_cost = estimate_call_cost_usd(
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        validated_text, error_code = parse_and_validate_llm_output(
            response.raw_text,
            metrics,
        )

        if validated_text is not None:
            ModelCallLog.objects.create(
                school=school,
                called_at=now,
                call_date=call_date,
                model_name=used_model,
                input_tokens=max(0, int(response.input_tokens)),
                output_tokens=max(0, int(response.output_tokens)),
                estimated_cost_usd=estimated_cost,
                result=ModelCallLog.Result.SUCCESS,
                error_code="",
            )
            gen_path = (
                DailySummary.GenerationPath.MODELE
                if attempt == 1
                else DailySummary.GenerationPath.MODELE_APRES_RELANCE
            )
            return _persist_summary(
                school=school,
                classroom=classroom,
                target_date=target_date,
                metrics=metrics,
                metrics_hash=metrics_hash,
                prompt_version=prompt_version,
                model_name=model_name,
                summary_text=validated_text,
                origin=DailySummary.Origin.MODEL,
                generation_path=gen_path,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        # Invalid/lying LLM output: log failed call and retry at most once.
        ModelCallLog.objects.create(
            school=school,
            called_at=now,
            call_date=call_date,
            model_name=used_model,
            input_tokens=max(0, int(response.input_tokens)),
            output_tokens=max(0, int(response.output_tokens)),
            estimated_cost_usd=estimated_cost,
            result=ModelCallLog.Result.INVALID_OUTPUT,
            error_code=error_code[:64],
        )
        retry_reason = error_code

    # Both attempts failed validation -> deterministic template fallback (SYN-04, SYN-05).
    return _persist_summary(
        school=school,
        classroom=classroom,
        target_date=target_date,
        metrics=metrics,
        metrics_hash=metrics_hash,
        prompt_version=prompt_version,
        model_name=model_name,
        summary_text=template_text,
        origin=DailySummary.Origin.TEMPLATE,
        generation_path=DailySummary.GenerationPath.GABARIT_APRES_ECHEC,
        actor=actor,
        ip_address=ip_address,
        now=now,
    )
