"""URL routing for the Passage application."""

from django.urls import path

from passage.views import (
    agent_checkpoints_api,
    agent_scan_view,
    agent_sync_api,
    badge_activate_view,
    home_view,
    login_view,
    logout_view,
    manager_anomaly_handle,
    manager_audit_list,
    manager_badge_activation_link,
    manager_badge_change_status,
    manager_badge_issue,
    manager_badge_list,
    manager_checkpoint_create,
    manager_dashboard,
    manager_demo_bench_codes_api,
    manager_demo_bench_view,
    manager_route_window_create,
    manager_student_create,
    manager_student_detail,
    manager_summary_view,
    my_badge_view,
    parent_dashboard,
    parent_student_detail,
    pwa_manifest_view,
    pwa_service_worker_view,
    server_time_api,
)

app_name = "passage"

urlpatterns = [
    path("", home_view, name="home"),
    path("manifest.webmanifest", pwa_manifest_view, name="pwa_manifest"),
    path("sw.js", pwa_service_worker_view, name="pwa_service_worker"),
    path("connexion/", login_view, name="login"),
    path("deconnexion/", logout_view, name="logout"),
    # Public badge support & time recalibration (students are not authenticated users)
    path("mon-badge/", my_badge_view, name="my_badge"),
    path("badge/activer/<str:token>/", badge_activate_view, name="badge_activate"),
    path("api/time/", server_time_api, name="api_server_time"),
    # Agent (single screen + offline sync API — AUT-06, SCN-01..11)
    path("scan/", agent_scan_view, name="agent_scan"),
    path("api/checkpoints/", agent_checkpoints_api, name="api_agent_checkpoints"),
    path("api/sync/", agent_sync_api, name="api_agent_sync"),
    # Parent (strictly scoped to own children — CON-01, SEC-05)
    path("parent/", parent_dashboard, name="parent_dashboard"),
    path("parent/eleves/<int:student_id>/", parent_student_detail, name="parent_student_detail"),
    # Manager (strictly scoped to own school — CON-02, GES-05, BAD-01..07)
    path("responsable/", manager_dashboard, name="manager_dashboard"),
    path("responsable/badges/", manager_badge_list, name="manager_badge_list"),
    path(
        "responsable/eleves/<int:student_id>/badge/emettre/",
        manager_badge_issue,
        name="manager_badge_issue",
    ),
    path(
        "responsable/badges/<int:badge_id>/etat/",
        manager_badge_change_status,
        name="manager_badge_change_status",
    ),
    path(
        "responsable/badges/<int:badge_id>/lien-activation/",
        manager_badge_activation_link,
        name="manager_badge_activation_link",
    ),
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
    path(
        "responsable/anomalies/<int:anomaly_id>/traiter/",
        manager_anomaly_handle,
        name="manager_anomaly_handle",
    ),
    path("responsable/synthese/", manager_summary_view, name="manager_summary"),
    path("responsable/demo/", manager_demo_bench_view, name="manager_demo_bench"),
    path("responsable/demo/codes/", manager_demo_bench_codes_api, name="manager_demo_bench_codes"),
    path("responsable/audit/", manager_audit_list, name="manager_audit_list"),
]
