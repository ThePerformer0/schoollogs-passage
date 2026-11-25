"""Public exports for Passage HTTP views."""

from passage.views.agent import agent_scan_view
from passage.views.api import (
    agent_checkpoints_api,
    agent_sync_api,
    server_time_api,
)
from passage.views.auth import home_view, login_view, logout_view
from passage.views.badges import (
    badge_activate_view,
    manager_badge_activation_link,
    manager_badge_change_status,
    manager_badge_issue,
    manager_badge_list,
    my_badge_view,
)
from passage.views.health import health_check
from passage.views.manager import (
    manager_audit_list,
    manager_checkpoint_create,
    manager_dashboard,
    manager_route_window_create,
    manager_student_create,
    manager_student_detail,
)
from passage.views.parent import parent_dashboard, parent_student_detail
from passage.views.pwa import pwa_manifest_view, pwa_service_worker_view

__all__ = [
    "agent_checkpoints_api",
    "agent_scan_view",
    "agent_sync_api",
    "badge_activate_view",
    "health_check",
    "home_view",
    "login_view",
    "logout_view",
    "manager_audit_list",
    "manager_badge_activation_link",
    "manager_badge_change_status",
    "manager_badge_issue",
    "manager_badge_list",
    "manager_checkpoint_create",
    "manager_dashboard",
    "manager_route_window_create",
    "manager_student_create",
    "manager_student_detail",
    "my_badge_view",
    "parent_dashboard",
    "parent_student_detail",
    "pwa_manifest_view",
    "pwa_service_worker_view",
    "server_time_api",
]
