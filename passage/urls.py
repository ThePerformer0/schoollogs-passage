"""URL routing for the Passage application."""

from django.urls import path

from passage.views import (
    agent_scan_view,
    home_view,
    login_view,
    logout_view,
    manager_audit_list,
    manager_checkpoint_create,
    manager_dashboard,
    manager_route_window_create,
    manager_student_create,
    manager_student_detail,
    parent_dashboard,
    parent_student_detail,
)

app_name = "passage"

urlpatterns = [
    path("", home_view, name="home"),
    path("connexion/", login_view, name="login"),
    path("deconnexion/", logout_view, name="logout"),
    # Agent (single screen — AUT-06, CON-03)
    path("scan/", agent_scan_view, name="agent_scan"),
    # Parent (strictly scoped to own children — CON-01, SEC-05)
    path("parent/", parent_dashboard, name="parent_dashboard"),
    path("parent/eleves/<int:student_id>/", parent_student_detail, name="parent_student_detail"),
    # Manager (strictly scoped to own school — CON-02, GES-05)
    path("responsable/", manager_dashboard, name="manager_dashboard"),
    path(
        "responsable/eleves/nouveau/",
        manager_student_create,
        name="manager_student_create",
    ),
    path(
        "responsable/eleves/<int:student_id>/",
        manager_student_detail,
        name="manager_student_detail",
    ),
    path(
        "responsable/points/nouveau/",
        manager_checkpoint_create,
        name="manager_checkpoint_create",
    ),
    path(
        "responsable/itineraires/nouveau/",
        manager_route_window_create,
        name="manager_route_window_create",
    ),
    path("responsable/audit/", manager_audit_list, name="manager_audit_list"),
]
