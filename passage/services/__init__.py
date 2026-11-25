"""Business logic services for the Passage module."""

from passage.services.audit import log_audit_event
from passage.services.auth import (
    UNIFORM_LOGIN_ERROR,
    AuthResult,
    authenticate_and_login,
    get_client_ip,
    get_home_url_for_user,
    is_login_locked,
    logout_user,
)
from passage.services.badges import (
    ActivationLinkError,
    BadgeError,
    BadgeTransitionError,
    ParsedBadgeCode,
    change_badge_status,
    compute_time_step,
    consume_activation_link,
    create_activation_link,
    generate_current_badge_code,
    inspect_activation_link,
    is_time_step_fresh,
    issue_badge,
    parse_badge_code,
    sign_badge_code,
    verify_badge_signature,
)
from passage.services.clock import (
    ClockEvaluation,
    compute_clock_offset,
    resolve_passage_timestamps,
)
from passage.services.sync import SyncItemResult, SyncPayloadError, sync_scan_batch

__all__ = [
    "ActivationLinkError",
    "AuthResult",
    "BadgeError",
    "BadgeTransitionError",
    "ClockEvaluation",
    "ParsedBadgeCode",
    "SyncItemResult",
    "SyncPayloadError",
    "UNIFORM_LOGIN_ERROR",
    "authenticate_and_login",
    "change_badge_status",
    "compute_clock_offset",
    "compute_time_step",
    "consume_activation_link",
    "create_activation_link",
    "generate_current_badge_code",
    "get_client_ip",
    "get_home_url_for_user",
    "inspect_activation_link",
    "is_login_locked",
    "is_time_step_fresh",
    "issue_badge",
    "log_audit_event",
    "logout_user",
    "parse_badge_code",
    "resolve_passage_timestamps",
    "sign_badge_code",
    "sync_scan_batch",
    "verify_badge_signature",
]
