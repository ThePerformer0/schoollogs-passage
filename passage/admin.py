"""Restricted Django Admin site reserved to the Manager role (GES-05, GES-06, SEC-10)."""

from __future__ import annotations

from functools import update_wrapper

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Q
from django.shortcuts import redirect
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from passage.models import (
    Anomaly,
    AuditLog,
    Badge,
    BadgeActivationLink,
    BadgeStatusHistory,
    Checkpoint,
    ClockOffset,
    DailySummary,
    ModelCallLog,
    OutgoingMessage,
    ParentStudentLink,
    Passage,
    RouteWindow,
    School,
    Student,
    User,
)
from passage.permissions import deny_access


def _is_manager_or_superuser(user: User) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.role == User.Role.MANAGER and user.school_id is not None


class PassageAdminSite(admin.AdminSite):
    """Custom AdminSite strictly reserved to Managers (and superusers), never Agents/Parents."""

    site_header = "SchoolLogs Passage — Administration Responsable"
    site_title = "Administration Passage"
    index_title = "Gestion de l'établissement"

    def has_permission(self, request) -> bool:
        return _is_manager_or_superuser(request.user)

    def admin_view(self, view, cacheable=False):
        def inner(request, *args, **kwargs):
            if not request.user.is_authenticated:
                query = urlencode({"next": request.get_full_path()})
                return redirect(f"{settings.LOGIN_URL}?{query}")
            if not self.has_permission(request):
                return deny_access(
                    request,
                    target_type="admin",
                    target_id=request.path,
                    details="Accès à l'administration refusé (réservé au Responsable).",
                )
            return view(request, *args, **kwargs)

        if not cacheable:
            inner = never_cache(inner)
        if not getattr(view, "csrf_exempt", False):
            inner = csrf_protect(inner)
        return update_wrapper(inner, view)


passage_admin_site = PassageAdminSite(name="passage_admin")


class SchoolScopedAdminMixin:
    """Enforce school-level tenant isolation across all Manager admin views (GES-05)."""

    school_lookup = "school"
    allow_add = True
    allow_change = True
    allow_delete = False

    def _obj_school_id(self, obj) -> int | None:
        if obj is None:
            return None
        if hasattr(obj, "school_id"):
            return obj.school_id
        if hasattr(obj, "student") and obj.student is not None:
            return obj.student.school_id
        if hasattr(obj, "badge") and obj.badge is not None:
            return obj.badge.student.school_id
        return None

    def _obj_in_manager_school(self, request, obj) -> bool:
        if request.user.is_superuser or obj is None:
            return True
        return self._obj_school_id(obj) == request.user.school_id

    def has_module_permission(self, request) -> bool:
        return _is_manager_or_superuser(request.user)

    def has_view_permission(self, request, obj=None) -> bool:
        return _is_manager_or_superuser(request.user) and self._obj_in_manager_school(
            request, obj
        )

    def has_add_permission(self, request) -> bool:
        return self.allow_add and _is_manager_or_superuser(request.user)

    def has_change_permission(self, request, obj=None) -> bool:
        return (
            self.allow_change
            and _is_manager_or_superuser(request.user)
            and self._obj_in_manager_school(request, obj)
        )

    def has_delete_permission(self, request, obj=None) -> bool:
        return (
            self.allow_delete
            and _is_manager_or_superuser(request.user)
            and self._obj_in_manager_school(request, obj)
        )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(**{self.school_lookup: request.user.school})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and request.user.school_id is not None:
            school = request.user.school
            if db_field.name == "school":
                kwargs["queryset"] = School.objects.filter(pk=school.pk)
            elif db_field.name == "student":
                kwargs["queryset"] = Student.objects.filter(school=school)
            elif db_field.name == "checkpoint":
                kwargs["queryset"] = Checkpoint.objects.filter(school=school)
            elif db_field.name == "badge":
                kwargs["queryset"] = Badge.objects.filter(student__school=school)
            elif db_field.name == "parent":
                kwargs["queryset"] = User.objects.filter(role=User.Role.PARENT)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change) -> None:
        if not request.user.is_superuser and hasattr(obj, "school_id"):
            obj.school = request.user.school
        obj.full_clean()
        super().save_model(request, obj, form, change)


class StudentAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("last_name", "first_name", "classroom", "school", "status")
    list_filter = ("status", "classroom")
    search_fields = ("first_name", "last_name", "classroom")
    allow_delete = False


class ParentStudentLinkAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "student__school"
    list_display = ("parent", "student", "created_at")
    search_fields = ("parent__username", "student__first_name", "student__last_name")
    allow_delete = True


class CheckpointAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    list_display = ("label", "checkpoint_type", "school", "is_active")
    list_filter = ("checkpoint_type", "is_active")
    search_fields = ("label",)


class RouteWindowAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "student__school"
    list_display = ("student", "order", "checkpoint", "start_time", "end_time", "weekdays", "is_active")
    list_filter = ("is_active", "checkpoint")
    allow_delete = True


class BadgeAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "student__school"
    # SEC-10: Never expose cryptographic badge secret in Django Admin.
    exclude = ("secret",)
    readonly_fields = ("public_id", "issued_at", "device_activated_at")
    list_display = ("public_id", "student", "status", "is_device_activated", "issued_at")
    list_filter = ("status", "is_device_activated")
    search_fields = ("public_id", "student__first_name", "student__last_name")


class BadgeStatusHistoryAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "badge__student__school"
    allow_add = False
    allow_change = False
    list_display = ("badge", "previous_status", "new_status", "changed_by", "changed_at")


class BadgeActivationLinkAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "badge__student__school"
    allow_add = False
    allow_change = False
    exclude = ("token_hash",)
    list_display = ("badge", "created_by", "created_at", "expires_at", "used_at")


class PassageAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = False
    list_display = (
        "retained_at",
        "student",
        "checkpoint",
        "status",
        "rejection_reason",
        "clock_suspect",
        "device_id",
    )
    list_filter = ("status", "clock_suspect", "rejection_reason")


class ClockOffsetAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    school_lookup = "agent__school"
    allow_add = False
    allow_change = False
    list_display = ("device_id", "agent", "offset_seconds", "is_suspect", "synced_at")


class AnomalyAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = True
    list_display = ("date", "student", "anomaly_type", "checkpoint", "status", "detected_at")
    list_filter = ("anomaly_type", "status", "date")


class OutgoingMessageAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = False
    list_display = ("target_date", "parent", "student", "status", "non_delivery_reason", "created_at")
    list_filter = ("status", "target_date")


class DailySummaryAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = False
    list_display = ("date", "classroom", "origin", "generation_path", "created_at")
    list_filter = ("origin", "generation_path", "date")


class ModelCallLogAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = False
    list_display = (
        "called_at",
        "model_name",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
        "result",
    )
    list_filter = ("result", "model_name")


class AuditLogAdmin(SchoolScopedAdminMixin, admin.ModelAdmin):
    allow_add = False
    allow_change = False
    list_display = ("occurred_at", "actor_identifier", "action", "target_type", "target_id", "outcome")
    list_filter = ("action", "outcome")


class PassageUserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "role", "school", "is_active")
    list_filter = ("role", "is_active")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Rôle et établissement", {"fields": ("full_name", "role", "school")}),
    )

    def has_module_permission(self, request) -> bool:
        return _is_manager_or_superuser(request.user)

    def has_view_permission(self, request, obj=None) -> bool:
        if not _is_manager_or_superuser(request.user):
            return False
        if request.user.is_superuser or obj is None:
            return True
        return obj.school_id == request.user.school_id or (
            obj.role == User.Role.PARENT
            and obj.student_links.filter(student__school_id=request.user.school_id).exists()
        )

    def has_add_permission(self, request) -> bool:
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None) -> bool:
        return request.user.is_superuser

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(
            Q(school_id=request.user.school_id)
            | Q(
                role=User.Role.PARENT,
                student_links__student__school_id=request.user.school_id,
            )
        ).distinct()


class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "timezone", "is_active")

    def has_module_permission(self, request) -> bool:
        return bool(request.user.is_authenticated and request.user.is_superuser)


passage_admin_site.register(School, SchoolAdmin)
passage_admin_site.register(User, PassageUserAdmin)
passage_admin_site.register(Student, StudentAdmin)
passage_admin_site.register(ParentStudentLink, ParentStudentLinkAdmin)
passage_admin_site.register(Checkpoint, CheckpointAdmin)
passage_admin_site.register(RouteWindow, RouteWindowAdmin)
passage_admin_site.register(Badge, BadgeAdmin)
passage_admin_site.register(BadgeStatusHistory, BadgeStatusHistoryAdmin)
passage_admin_site.register(BadgeActivationLink, BadgeActivationLinkAdmin)
passage_admin_site.register(Passage, PassageAdmin)
passage_admin_site.register(ClockOffset, ClockOffsetAdmin)
passage_admin_site.register(Anomaly, AnomalyAdmin)
passage_admin_site.register(OutgoingMessage, OutgoingMessageAdmin)
passage_admin_site.register(DailySummary, DailySummaryAdmin)
passage_admin_site.register(ModelCallLog, ModelCallLogAdmin)
passage_admin_site.register(AuditLog, AuditLogAdmin)
