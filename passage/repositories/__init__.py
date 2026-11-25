"""Data-access layer enforcing tenant (school) and parent isolation."""

from passage.repositories.auth_repo import (
    clear_or_supersede_note,
    get_recent_failures_since_last_success,
    record_login_attempt,
)
from passage.repositories.school_repo import (
    get_anomaly_for_manager,
    get_checkpoint_for_manager,
    get_student_for_manager,
    get_student_for_parent,
    list_anomalies_for_school,
    list_anomalies_for_student,
    list_audit_logs_for_school,
    list_checkpoints_for_school,
    list_outgoing_messages_for_school,
    list_passages_for_school,
    list_passages_for_student,
    list_rejected_passages_for_school,
    list_students_for_parent,
    list_students_for_school,
)

__all__ = [
    "clear_or_supersede_note",
    "get_anomaly_for_manager",
    "get_checkpoint_for_manager",
    "get_recent_failures_since_last_success",
    "get_student_for_manager",
    "get_student_for_parent",
    "list_anomalies_for_school",
    "list_anomalies_for_student",
    "list_audit_logs_for_school",
    "list_checkpoints_for_school",
    "list_outgoing_messages_for_school",
    "list_passages_for_school",
    "list_passages_for_student",
    "list_rejected_passages_for_school",
    "list_students_for_parent",
    "list_students_for_school",
    "record_login_attempt",
]
