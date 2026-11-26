"""Isolated notification delivery gateway interface (NOT-03, NOT-04)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from passage.models import Anomaly, Student, User


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """Outcome returned by a notification provider."""

    sent: bool
    sent_at: datetime | None = None
    non_delivery_reason: str = ""


class NotificationGateway(ABC):
    """Provider abstraction allowing a real SMS/email channel to plug in without changing domain code."""

    @abstractmethod
    def send(
        self,
        *,
        parent: User,
        student: Student,
        body: str,
        anomaly: Anomaly,
    ) -> NotificationDeliveryResult:
        """Attempt delivery of a single parent message."""


class DatabaseNotificationGateway(NotificationGateway):
    """Default local implementation recording delivery without external network traffic (NOT-03)."""

    def send(
        self,
        *,
        parent: User,
        student: Student,
        body: str,
        anomaly: Anomaly,
    ) -> NotificationDeliveryResult:
        return NotificationDeliveryResult(sent=True, sent_at=timezone.now())


def get_notification_gateway() -> NotificationGateway:
    """Return the active notification delivery gateway."""
    return DatabaseNotificationGateway()
