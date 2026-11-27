"""Template context processor exposing non-sensitive Passage feature flags."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest


def passage_flags(request: HttpRequest) -> dict[str, bool]:
    """Expose whether DEMO_MODE is active so the demo bench nav link stays hidden by default (DEM-03)."""
    return {
        "PASSAGE_DEMO_MODE": bool(getattr(settings, "PASSAGE_DEMO_MODE", False)),
    }
