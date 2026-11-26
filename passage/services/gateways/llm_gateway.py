"""Isolated external LLM client interface using Python stdlib only (SYN-06, SYN-07, SYN-12)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import socket
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


class LLMError(Exception):
    """Base error raised by an external LLM provider (never includes secrets)."""

    def __init__(self, error_code: str, message: str = "") -> None:
        self.error_code = (error_code or "provider_error")[:64]
        super().__init__(message or self.error_code)


class LLMTimeoutError(LLMError):
    """Raised when the external LLM provider does not respond before the timeout."""

    def __init__(self, error_code: str = "timeout", message: str = "Délai dépassé.") -> None:
        super().__init__(error_code=error_code, message=message)


class LLMServiceError(LLMError):
    """Raised when the external LLM provider fails or returns an unreadable response."""


@dataclass(frozen=True)
class LLMResponse:
    """Normalized response returned by an LLM gateway."""

    raw_text: str
    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMGateway(ABC):
    """Provider abstraction for generating a short natural-language summary from precomputed metrics."""

    @abstractmethod
    def generate_summary(
        self,
        *,
        system_instruction: str,
        metrics_payload: dict[str, Any],
        retry_reason: str | None = None,
    ) -> LLMResponse:
        """Invoke the LLM with precomputed aggregates only (zero student names)."""


class GeminiLLMGateway(LLMGateway):
    """Free-tier Gemini REST API client (stdlib urllib only, API key strictly in header)."""

    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, *, api_key: str, model_name: str, timeout_seconds: int = 10) -> None:
        self._api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = max(1, int(timeout_seconds))

    def generate_summary(
        self,
        *,
        system_instruction: str,
        metrics_payload: dict[str, Any],
        retry_reason: str | None = None,
    ) -> LLMResponse:
        safe_model = urllib_parse.quote(self.model_name, safe="-_.")
        url = f"{self.API_BASE}/{safe_model}:generateContent"

        user_parts = [
            "Agrégats calculés pour la classe (aucune donnée nominative) :",
            json.dumps(metrics_payload, ensure_ascii=False, sort_keys=True),
        ]
        if retry_reason:
            user_parts.append(
                "Correction requise après un premier essai non conforme : "
                f"{retry_reason}. Réponds strictement avec le JSON {{\"summary\": \"...\"}} "
                "et utilise uniquement les nombres fournis ci-dessus."
            )

        body = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": "\n".join(user_parts)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
                "maxOutputTokens": 256,
            },
        }

        req = urllib_request.Request(
            url=url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                # Key is sent in header only, never in URL query params (SEC-09 / AUD-03).
                "x-goog-api-key": self._api_key,
            },
        )

        try:
            with urllib_request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
        except (TimeoutError, socket.timeout) as exc:
            raise LLMTimeoutError("timeout", "Le service IA n'a pas répondu à temps.") from exc
        except urllib_error.HTTPError as exc:
            raise LLMServiceError(f"http_{exc.code}", f"Erreur HTTP {exc.code}.") from exc
        except urllib_error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise LLMTimeoutError("timeout", "Le service IA n'a pas répondu à temps.") from exc
            raise LLMServiceError("network_error", "Service IA indisponible.") from exc
        except OSError as exc:
            raise LLMServiceError("network_error", "Erreur réseau vers le service IA.") from exc

        try:
            parsed = json.loads(raw_body)
            candidates = parsed.get("candidates") or []
            parts = candidates[0]["content"]["parts"]
            raw_text = "".join(str(part.get("text", "")) for part in parts).strip()
            usage = parsed.get("usageMetadata") or {}
            input_tokens = max(0, int(usage.get("promptTokenCount") or 0))
            output_tokens = max(0, int(usage.get("candidatesTokenCount") or 0))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMServiceError(
                "malformed_provider_response",
                "Réponse fournisseur illisible.",
            ) from exc

        return LLMResponse(
            raw_text=raw_text,
            model_name=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def get_llm_gateway(*, api_key: str, model_name: str, timeout_seconds: int = 10) -> LLMGateway:
    """Return the active LLM gateway implementation."""
    return GeminiLLMGateway(
        api_key=api_key,
        model_name=model_name,
        timeout_seconds=timeout_seconds,
    )
