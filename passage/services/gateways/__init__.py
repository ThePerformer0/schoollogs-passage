"""External provider interfaces isolated from core domain logic (NOT-04, SYN-12)."""

from passage.services.gateways.llm_gateway import (
    GeminiLLMGateway,
    LLMError,
    LLMGateway,
    LLMResponse,
    LLMServiceError,
    LLMTimeoutError,
    get_llm_gateway,
)
from passage.services.gateways.notification_gateway import (
    DatabaseNotificationGateway,
    NotificationDeliveryResult,
    NotificationGateway,
    get_notification_gateway,
)

__all__ = [
    "DatabaseNotificationGateway",
    "GeminiLLMGateway",
    "LLMError",
    "LLMGateway",
    "LLMResponse",
    "LLMServiceError",
    "LLMTimeoutError",
    "NotificationDeliveryResult",
    "NotificationGateway",
    "get_llm_gateway",
    "get_notification_gateway",
]
