"""Django settings for the SchoolLogs Passage module."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load local .env if present without overriding explicit process environment variables.
load_dotenv(BASE_DIR / ".env", override=False)

DEV_FALLBACK_SECRET_KEY = "django-insecure-local-dev-only-change-before-deploy"
INSECURE_SECRET_PREFIXES = ("remplacer-", "django-insecure-")
MIN_PRODUCTION_SECRET_LENGTH = 32


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(
        f"Environment variable {name} must be a boolean (0/1, true/false), got {raw_value!r}."
    )


def _env_int(name: str, default: int, *, min_value: int = 0) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = int(raw_value.strip())
        except ValueError as exc:
            raise ImproperlyConfigured(
                f"Environment variable {name} must be an integer, got {raw_value!r}."
            ) from exc
    if value < min_value:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be >= {min_value}, got {value}."
        )
    return value


def _env_decimal(
    name: str,
    default: Decimal,
    *,
    min_value: Decimal = Decimal("0"),
) -> Decimal:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        value = default
    else:
        try:
            value = Decimal(raw_value.strip())
        except InvalidOperation as exc:
            raise ImproperlyConfigured(
                f"Environment variable {name} must be a valid decimal, got {raw_value!r}."
            ) from exc
    if value < min_value:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be >= {min_value}, got {value}."
        )
    return value


def _env_list(name: str, default: list[str]) -> list[str]:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return list(default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def resolve_secret_key(raw_key: str | None, *, debug: bool) -> str:
    """Validate and return SECRET_KEY, failing closed when DEBUG is disabled."""
    cleaned = (raw_key or "").strip()
    is_placeholder = (
        not cleaned
        or any(cleaned.startswith(prefix) for prefix in INSECURE_SECRET_PREFIXES)
    )

    if debug:
        return DEV_FALLBACK_SECRET_KEY if is_placeholder else cleaned

    if is_placeholder or len(cleaned) < MIN_PRODUCTION_SECRET_LENGTH:
        raise ImproperlyConfigured(
            "SECRET_KEY must be set to a unique random string of at least "
            f"{MIN_PRODUCTION_SECRET_LENGTH} characters when DEBUG is disabled."
        )
    return cleaned


def build_database_config(base_dir: Path) -> dict[str, object]:
    """Build Django default database config (SQLite WAL by default, PostgreSQL optional)."""
    engine_choice = os.getenv("DB_ENGINE", "sqlite").strip().lower()

    if engine_choice == "sqlite":
        sqlite_path_raw = os.getenv("SQLITE_PATH", "db.sqlite3").strip() or "db.sqlite3"
        sqlite_path = Path(sqlite_path_raw)
        if not sqlite_path.is_absolute():
            sqlite_path = base_dir / sqlite_path
        timeout_seconds = _env_int("SQLITE_TIMEOUT_SECONDS", 20, min_value=1)
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            "OPTIONS": {
                "timeout": timeout_seconds,
                "transaction_mode": "IMMEDIATE",
                "init_command": (
                    "PRAGMA journal_mode=WAL;"
                    "PRAGMA synchronous=NORMAL;"
                ),
            },
        }

    if engine_choice == "postgresql":
        db_name = os.getenv("DB_NAME", "").strip()
        db_user = os.getenv("DB_USER", "").strip()
        if not db_name or not db_user:
            raise ImproperlyConfigured(
                "DB_NAME and DB_USER must be set when DB_ENGINE=postgresql."
            )
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": db_name,
            "USER": db_user,
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }

    raise ImproperlyConfigured(
        f"Unsupported DB_ENGINE {engine_choice!r}. Expected 'sqlite' or 'postgresql'."
    )


# --- Core Django settings ----------------------------------------------------
DEBUG = _env_bool("DEBUG", default=False)
SECRET_KEY = resolve_secret_key(os.getenv("SECRET_KEY"), debug=DEBUG)
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = _env_list(
    "CSRF_TRUSTED_ORIGINS",
    ["http://localhost:8000", "http://127.0.0.1:8000"],
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "passage.apps.PassageConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "passage.context_processors.passage_flags",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database (SQLite WAL by default, PostgreSQL optional) -------------------
DATABASES = {
    "default": build_database_config(BASE_DIR),
}

# --- Custom user model -------------------------------------------------------
AUTH_USER_MODEL = "passage.User"
LOGIN_URL = "/connexion/"

# --- Password hashing (Argon2id default — AUT-02 / SEC-01) -------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# --- Passage domain configurable parameters (CDC section 9) ------------------
PASSAGE_CODE_STEP_SECONDS = _env_int("PAS_CODE", 30, min_value=5)
PASSAGE_CODE_STEP_TOLERANCE = _env_int("TOLERANCE_PAS", 1, min_value=0)
PASSAGE_GRACE_DELAY = timedelta(minutes=_env_int("DELAI_GRACE", 10, min_value=0))
PASSAGE_SCHEDULER_PERIOD_SECONDS = _env_int(
    "PERIODE_PLANIFICATEUR", 60, min_value=1
)
PASSAGE_DOUBLE_SCAN_INTERVAL_SECONDS = _env_int(
    "INTERVALLE_DOUBLE_SCAN", 60, min_value=1
)
PASSAGE_CLOCK_SKEW_THRESHOLD = timedelta(
    minutes=_env_int("SEUIL_HORLOGE", 5, min_value=1)
)
PASSAGE_MAX_AGE = timedelta(days=_env_int("AGE_MAX_PASSAGE", 7, min_value=1))
PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY = _env_int(
    "PLAFOND_MESSAGES", 3, min_value=1
)
PASSAGE_MAX_LOGIN_ATTEMPTS = _env_int("MAX_TENTATIVES", 5, min_value=1)
PASSAGE_LOGIN_ATTEMPTS_WINDOW = timedelta(
    minutes=_env_int("FENETRE_TENTATIVES", 15, min_value=1)
)
PASSAGE_LOGIN_LOCKOUT_DURATION = timedelta(
    minutes=_env_int("DUREE_BLOCAGE", 15, min_value=1)
)
PASSAGE_SESSION_DURATION = timedelta(
    hours=_env_int("DUREE_SESSION", 8, min_value=1)
)
PASSAGE_ACTIVATION_LINK_DURATION = timedelta(
    hours=_env_int("DUREE_LIEN_ACTIVATION", 24, min_value=1)
)
PASSAGE_AI_DAILY_CALL_LIMIT = _env_int("PLAFOND_APPELS_IA_JOUR", 20, min_value=0)
PASSAGE_AI_DAILY_COST_LIMIT_USD = _env_decimal(
    "PLAFOND_COUT_IA_JOUR", Decimal("0.50"), min_value=Decimal("0")
)
PASSAGE_AI_API_KEY = os.getenv("CLE_API_IA", "").strip()
PASSAGE_AI_MODEL_NAME = (
    os.getenv("IA_MODELE", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
)
PASSAGE_AI_TIMEOUT_SECONDS = _env_int("IA_TIMEOUT_SECONDS", 10, min_value=1)
PASSAGE_AI_INPUT_COST_PER_1M_USD = _env_decimal(
    "IA_TARIF_ENTREE_1M", Decimal("0.15"), min_value=Decimal("0")
)
PASSAGE_AI_OUTPUT_COST_PER_1M_USD = _env_decimal(
    "IA_TARIF_SORTIE_1M", Decimal("0.60"), min_value=Decimal("0")
)
PASSAGE_AI_PROMPT_VERSION = "v1"
PASSAGE_DEMO_MODE = _env_bool("DEMO_MODE", default=False)

PASSAGE_SETTINGS = {
    "code_step_seconds": PASSAGE_CODE_STEP_SECONDS,
    "code_step_tolerance": PASSAGE_CODE_STEP_TOLERANCE,
    "grace_delay": PASSAGE_GRACE_DELAY,
    "scheduler_period_seconds": PASSAGE_SCHEDULER_PERIOD_SECONDS,
    "double_scan_interval_seconds": PASSAGE_DOUBLE_SCAN_INTERVAL_SECONDS,
    "clock_skew_threshold": PASSAGE_CLOCK_SKEW_THRESHOLD,
    "max_passage_age": PASSAGE_MAX_AGE,
    "max_messages_per_student_per_day": PASSAGE_MAX_MESSAGES_PER_STUDENT_PER_DAY,
    "max_login_attempts": PASSAGE_MAX_LOGIN_ATTEMPTS,
    "login_attempts_window": PASSAGE_LOGIN_ATTEMPTS_WINDOW,
    "login_lockout_duration": PASSAGE_LOGIN_LOCKOUT_DURATION,
    "session_duration": PASSAGE_SESSION_DURATION,
    "activation_link_duration": PASSAGE_ACTIVATION_LINK_DURATION,
    "ai_daily_call_limit": PASSAGE_AI_DAILY_CALL_LIMIT,
    "ai_daily_cost_limit_usd": PASSAGE_AI_DAILY_COST_LIMIT_USD,
    "ai_api_key_configured": bool(PASSAGE_AI_API_KEY),
    "ai_model_name": PASSAGE_AI_MODEL_NAME,
    "ai_timeout_seconds": PASSAGE_AI_TIMEOUT_SECONDS,
    "demo_mode": PASSAGE_DEMO_MODE,
}

# --- Session, CSRF, and HTTP security headers --------------------------------
SESSION_COOKIE_AGE = int(PASSAGE_SESSION_DURATION.total_seconds())
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_SAVE_EVERY_REQUEST = False

# Kept False so the PWA native JavaScript client can read the CSRF token cookie
# and attach X-CSRFToken on sync POST requests (SEC-08).
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# --- Internationalization and time zone (UTC storage, Africa/Douala display) -
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Douala"
USE_I18N = True
USE_TZ = True

# --- Static files ------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
