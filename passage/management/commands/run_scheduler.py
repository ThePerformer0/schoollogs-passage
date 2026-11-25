"""Autonomous background loop detecting POINT_MANQUANT anomalies without web traffic (ANO-02, ANO-03)."""

from __future__ import annotations

import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from passage.services.anomalies import detect_missing_points


class Command(BaseCommand):
    help = (
        "Exécute le planificateur autonome de détection des points manquants "
        "(idempotent, en boucle toutes les PERIODE_PLANIFICATEUR secondes, ou une fois avec --once)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--once",
            action="store_true",
            help="Exécute un seul cycle de détection puis quitte.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Intervalle en secondes entre deux cycles (par défaut : PERIODE_PLANIFICATEUR).",
        )
        parser.add_argument(
            "--max-iterations",
            type=int,
            default=None,
            help="Nombre maximal de cycles à exécuter (utile pour les tests).",
        )

    def handle(self, *args, **options) -> None:
        run_once: bool = bool(options["once"])
        max_iterations: int | None = options["max_iterations"]
        interval: int = (
            options["interval"]
            if options["interval"] is not None
            else int(settings.PASSAGE_SCHEDULER_PERIOD_SECONDS)
        )
        if interval < 1:
            interval = 1

        if run_once:
            max_iterations = 1

        stop_requested = False

        def _handle_signal(signum, frame) -> None:
            nonlocal stop_requested
            stop_requested = True

        # Register graceful shutdown handlers when invoked on the main thread.
        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except ValueError:
            pass

        iteration = 0
        while not stop_requested:
            iteration += 1
            close_old_connections()
            now = timezone.now()

            try:
                result = detect_missing_points(reference_time=now)
                self.stdout.write(
                    f"[{now.isoformat()}] Cycle #{iteration} : "
                    f"{result.checked_windows} fenêtre(s) vérifiée(s), "
                    f"{result.missing_created} anomalie(s) créée(s), "
                    f"{result.missing_resolved} résolue(s)."
                )
            except Exception as exc:
                if run_once:
                    raise
                self.stderr.write(
                    self.style.ERROR(f"[{now.isoformat()}] Erreur durant le cycle #{iteration} : {exc}")
                )

            if max_iterations is not None and iteration >= max_iterations:
                break

            # Sleep in short increments so Ctrl+C / SIGTERM exits promptly.
            remaining = float(interval)
            while remaining > 0 and not stop_requested:
                step = min(0.5, remaining)
                time.sleep(step)
                remaining -= step
