"""Public exports for Passage HTTP views."""

from passage.views.agent import agent_scan_view
from passage.views.auth import home_view, login_view, logout_view
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

__all__ = [
    "agent_scan_view",
    "health_check",
    "home_view",
    "login_view",
    "logout_view",
    "manager_audit_list",
    "manager_checkpoint_create",
    "manager_dashboard",
    "manager_route_window_create",
    "manager_student_create",
    "manager_student_detail",
    "parent_dashboard",
    "parent_student_detail",
]
