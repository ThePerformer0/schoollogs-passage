"""Manager view for AI-assisted daily classroom summaries (CON-02, SYN-01..10)."""

from __future__ import annotations

from datetime import date
import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from passage.models import User
from passage.permissions import deny_access, role_required
from passage.repositories import school_repo
from passage.services.auth import get_client_ip
from passage.services.summaries import (
    generate_daily_summary,
    get_daily_ai_usage,
)


def _wants_json(request: HttpRequest) -> bool:
    accept = request.headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


STRICT_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_iso_date(raw_value: str | None, *, default_date: date) -> date | None:
    cleaned = (raw_value or "").strip()
    if not cleaned:
        return default_date
    if not STRICT_ISO_DATE_RE.fullmatch(cleaned):
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


@require_http_methods(["GET", "POST"])
@role_required(User.Role.MANAGER)
def manager_summary_view(request: HttpRequest) -> HttpResponse:
    """Display and generate daily summaries for classrooms in the manager's school (SYN-01..10)."""
    if request.user.school_id is None:
        return deny_access(
            request,
            target_type="daily_summary",
            details="Responsable sans établissement rattaché.",
        )

    school = request.user.school
    school_tz = ZoneInfo(school.timezone)
    today_local = timezone.now().astimezone(school_tz).date()
    classrooms = school_repo.list_classrooms_for_school(school.id)

    selected_classroom = classrooms[0] if classrooms else ""
    selected_date = today_local
    current_summary = None
    error_message = ""

    if request.method == "POST":
        raw_classroom = (request.POST.get("classroom") or "").strip()
        raw_date = request.POST.get("date")
        parsed_date = _parse_iso_date(raw_date, default_date=today_local)

        if not raw_classroom:
            error_message = "Veuillez sélectionner une classe."
            if _wants_json(request):
                return JsonResponse({"error": "missing_classroom", "detail": error_message}, status=400)
        elif parsed_date is None:
            error_message = "Format de date invalide (attendu : AAAA-MM-JJ)."
            if _wants_json(request):
                return JsonResponse({"error": "invalid_date", "detail": error_message}, status=400)
        elif raw_classroom not in classrooms:
            return deny_access(
                request,
                target_type="classroom",
                target_id=raw_classroom[:64],
                details="Tentative de génération de synthèse pour une classe hors établissement.",
            )
        else:
            selected_classroom = raw_classroom
            selected_date = parsed_date
            current_summary = generate_daily_summary(
                school=school,
                classroom=selected_classroom,
                target_date=selected_date,
                actor=request.user,
                ip_address=get_client_ip(request),
            )
            if _wants_json(request):
                return JsonResponse(
                    {
                        "summary_id": current_summary.id,
                        "classroom": current_summary.classroom,
                        "date": current_summary.date.isoformat(),
                        "summary_text": current_summary.summary_text,
                        "origin": current_summary.origin,
                        "generation_path": current_summary.generation_path,
                        "computed_metrics": current_summary.computed_metrics,
                    },
                    status=200,
                )
    else:
        query_classroom = (request.GET.get("classroom") or "").strip()
        if query_classroom in classrooms:
            selected_classroom = query_classroom
        parsed_query_date = _parse_iso_date(request.GET.get("date"), default_date=today_local)
        if parsed_query_date is not None:
            selected_date = parsed_query_date

    recent_summaries = list(school_repo.list_daily_summaries_for_school(school.id))
    if current_summary is None and recent_summaries:
        current_summary = recent_summaries[0]

    calls_today, cost_today = get_daily_ai_usage(today_local)
    context = {
        "school": school,
        "classrooms": classrooms,
        "selected_classroom": selected_classroom,
        "selected_date": selected_date.isoformat(),
        "current_summary": current_summary,
        "recent_summaries": recent_summaries,
        "error_message": error_message,
        "ai_key_configured": bool((getattr(settings, "PASSAGE_AI_API_KEY", "") or "").strip()),
        "ai_calls_today": calls_today,
        "ai_call_limit": int(getattr(settings, "PASSAGE_AI_DAILY_CALL_LIMIT", 20)),
        "ai_cost_today": cost_today,
        "ai_cost_limit": getattr(settings, "PASSAGE_AI_DAILY_COST_LIMIT_USD", 0),
    }
    status_code = 400 if error_message else 200
    return render(request, "passage/manager_summary.html", context, status=status_code)
