"""Central configuration. Every environment variable the app reads is declared
here, so nothing else in the codebase touches os.environ directly.

Secrets (GEMINI_API_KEY, credentials) never leave the server. The FIREBASE_*
web values ARE public by design - the Firebase JS SDK ships them to every
browser - so they are safe to serve from /api/config. Access control comes
from Firestore rules and server-side token verification, not from hiding them.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # no-op on Cloud Run, where real env vars are already set


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Server ---------------------------------------------------------------
PORT = _int_env("PORT", 8080)
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}

# --- Gemini ---------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
GEMINI_TIMEOUT_S = _int_env("GEMINI_TIMEOUT_S", 90)

# Fallback chain. Flash models return a transient 503 ("experiencing high
# demand") often enough that a single-model client fails a live demo roughly
# one run in three. If the primary is unavailable we transparently try the
# next one rather than showing the user an error. Order is fastest first.
# Override with a comma-separated list if a model is retired.
GEMINI_FALLBACK_MODELS = [
    name.strip()
    for name in os.environ.get(
        "GEMINI_FALLBACK_MODELS", "gemini-3.6-flash,gemini-3.5-flash"
    ).split(",")
    if name.strip()
]

# --- Firebase web SDK config (public) ------------------------------------
FIREBASE_WEB_CONFIG = {
    "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
    "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
    "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
    "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
    "appId": os.environ.get("FIREBASE_APP_ID", ""),
}

# --- Input limits ---------------------------------------------------------
# Caps exist for cost control and to keep a single request from monopolising a
# gunicorn worker. They are enforced server-side; the UI mirrors them.
MAX_CONTENT_LENGTH = _int_env("MAX_CONTENT_LENGTH", 6 * 1024 * 1024)  # whole request
MAX_UPLOAD_BYTES = _int_env("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)      # single PDF
MAX_RESUME_CHARS = _int_env("MAX_RESUME_CHARS", 20000)
MAX_JD_CHARS = _int_env("MAX_JD_CHARS", 20000)
MAX_SHORT_FIELD_CHARS = 200      # company, role, location
MAX_NOTES_CHARS = 4000
MIN_RESUME_CHARS = 120           # below this an analysis is meaningless
MIN_JD_CHARS = 120

# --- Product limits -------------------------------------------------------
HISTORY_PAGE_SIZE = 50
MAX_COMPARE_ITEMS = 3

ALLOWED_STATUSES = (
    "saved",
    "applied",
    "assessment",
    "interview",
    "rejected",
    "offer",
)


def missing_required() -> list[str]:
    """Config problems worth warning about at boot (not fatal - /healthz and the
    landing page must still work so a broken deploy is diagnosable)."""
    problems = []
    if not GEMINI_API_KEY:
        problems.append("GEMINI_API_KEY is not set - analysis will fail")
    if not FIREBASE_WEB_CONFIG["apiKey"] or not FIREBASE_WEB_CONFIG["projectId"]:
        problems.append("FIREBASE_API_KEY / FIREBASE_PROJECT_ID not set - sign-in will fail")
    return problems
