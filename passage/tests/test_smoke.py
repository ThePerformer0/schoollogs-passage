"""Smoke tests validating the Django foundation, SQLite WAL, Argon2, and defaults."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import TestCase

from config.settings import resolve_secret_key


class FoundationSmokeTests(TestCase):
    """Verify the baseline technical and security configuration of the project."""

    def test_django_system_checks_pass(self) -> None:
        call_command("check")

    def test_health_endpoint_returns_ok_with_security_headers(self) -> None:
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "ok"})
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_argon2_is_default_password_hasher(self) -> None:
        self.assertEqual(
            settings.PASSWORD_HASHERS[0],
            "django.contrib.auth.hashers.Argon2PasswordHasher",
        )
        encoded = make_password("MotDePasseTest!2026")
        self.assertTrue(
            encoded.startswith("argon2$argon2id$"),
            f"Expected Argon2id hash prefix, got: {encoded[:24]!r}",
        )
        self.assertTrue(check_password("MotDePasseTest!2026", encoded))
        self.assertFalse(check_password("MauvaisMotDePasse!2026", encoded))

    def test_sqlite_configuration_enables_wal_and_timeout(self) -> None:
        default_db = settings.DATABASES["default"]
        if default_db["ENGINE"] != "django.db.backends.sqlite3":
            self.skipTest("Non-SQLite database backend configured.")

        options = default_db.get("OPTIONS", {})
        timeout_seconds = options.get("timeout")
        self.assertIsInstance(timeout_seconds, int)
        self.assertGreaterEqual(timeout_seconds, 10)
        self.assertEqual(options.get("transaction_mode"), "IMMEDIATE")
        self.assertIn("PRAGMA journal_mode=WAL;", options.get("init_command", ""))

        # Verify PRAGMA journal_mode=WAL and busy_timeout on a real on-disk SQLite file,
        # because SQLite ignores WAL mode on Django's default in-memory test database.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "wal_check.sqlite3"
            wrapper_settings = deepcopy(default_db)
            wrapper_settings["NAME"] = str(db_path)
            wrapper = DatabaseWrapper(wrapper_settings, alias="wal_smoke")
            try:
                with wrapper.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode;")
                    journal_mode = cursor.fetchone()[0]
                    cursor.execute("PRAGMA busy_timeout;")
                    busy_timeout_ms = cursor.fetchone()[0]
                    cursor.execute("PRAGMA foreign_keys;")
                    foreign_keys_enabled = cursor.fetchone()[0]
            finally:
                wrapper.close()

        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(busy_timeout_ms, timeout_seconds * 1000)
        self.assertEqual(foreign_keys_enabled, 1)

    def test_timezone_and_utc_storage(self) -> None:
        self.assertEqual(settings.TIME_ZONE, "Africa/Douala")
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(str(ZoneInfo(settings.TIME_ZONE)), "Africa/Douala")

    def test_default_domain_parameters_and_security_flags(self) -> None:
        self.assertEqual(settings.PASSAGE_CODE_STEP_SECONDS, 30)
        self.assertEqual(settings.PASSAGE_CODE_STEP_TOLERANCE, 1)
        self.assertEqual(settings.PASSAGE_GRACE_DELAY, timedelta(minutes=10))
        self.assertEqual(settings.PASSAGE_SCHEDULER_PERIOD_SECONDS, 60)
        self.assertEqual(settings.PASSAGE_DOUBLE_SCAN_INTERVAL_SECONDS, 60)
        self.assertEqual(settings.PASSAGE_CLOCK_SKEW_THRESHOLD, timedelta(minutes=5))
        self.assertEqual(settings.PASSAGE_MAX_AGE, timedelta(days=7))
        self.assertEqual(settings.PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY, 3)
        self.assertEqual(settings.PASSAGE_MAX_LOGIN_ATTEMPTS, 5)
        self.assertEqual(settings.PASSAGE_LOGIN_ATTEMPTS_WINDOW, timedelta(minutes=15))
        self.assertEqual(settings.PASSAGE_LOGIN_LOCKOUT_DURATION, timedelta(minutes=15))
        self.assertEqual(settings.PASSAGE_SESSION_DURATION, timedelta(hours=8))
        self.assertEqual(settings.PASSAGE_ACTIVATION_LINK_DURATION, timedelta(hours=24))
        self.assertEqual(settings.PASSAGE_AI_DAILY_CALL_LIMIT, 20)
        self.assertEqual(settings.PASSAGE_AI_DAILY_COST_LIMIT_USD, Decimal("0.50"))
        self.assertFalse(settings.PASSAGE_DEMO_MODE)

        self.assertEqual(settings.SESSION_COOKIE_AGE, 8 * 3600)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertFalse(settings.CSRF_COOKIE_HTTPONLY)

    def test_secret_key_validation_fails_closed_when_debug_is_disabled(self) -> None:
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key("", debug=False)
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(
                "remplacer-par-une-cle-aleatoire-de-50-caracteres-minimum",
                debug=False,
            )
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key("too-short-secret", debug=False)

        valid_key = "a" * 40
        self.assertEqual(resolve_secret_key(valid_key, debug=False), valid_key)
        self.assertTrue(resolve_secret_key("", debug=True).startswith("django-insecure-"))
