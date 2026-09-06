"""Verifies Firebase Auth ID tokens sent by the frontend.

The frontend (Firebase JS SDK) signs the user in, gets an ID token, and sends it
as `Authorization: Bearer <id_token>`. This module proves that token is real and
attaches the user's uid to Flask's `g`.

Two properties worth stating explicitly, because they are what make the whole
security model work:

* The browser NEVER receives privileged credentials. It holds a short-lived ID
  token scoped to one user; all Firestore writes happen server-side under the
  Admin SDK.
* Bearer tokens are immune to CSRF by construction - a cross-site form post
  cannot attach an Authorization header. There is no session cookie to forge.
"""
import logging
from functools import wraps

from flask import g, jsonify, request

import firebase_admin
from firebase_admin import auth as firebase_auth_admin

logger = logging.getLogger(__name__)

_initialized = False

# Small tolerance for a browser clock running slightly ahead of Google's. Without
# it, a user whose laptop is 30 seconds fast gets "token used too early" and an
# inexplicable sign-in loop - a classic demo-day failure.
_CLOCK_SKEW_SECONDS = 15


def ensure_firebase_app():
    """Initialize the Firebase Admin SDK exactly once, on first real use.

    Application Default Credentials means:
      - On Cloud Run: works automatically via the service identity, no key file.
      - Locally: set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON.
    """
    global _initialized
    if not _initialized:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _initialized = True


def _extract_bearer_token() -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return ""
    return header.split("Bearer ", 1)[1].strip()


def login_required(f):
    """Reject the request unless it carries a valid, unexpired Firebase ID token.

    Responses distinguish an EXPIRED token (401 + code `token_expired`, which the
    frontend handles by silently refreshing and retrying once) from a genuinely
    invalid one (401 + `unauthenticated`, which sends the user back to sign-in).
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        ensure_firebase_app()

        id_token = _extract_bearer_token()
        if not id_token:
            return (
                jsonify(
                    {
                        "error": "You need to be signed in to do that.",
                        "code": "unauthenticated",
                    }
                ),
                401,
            )

        try:
            decoded = firebase_auth_admin.verify_id_token(
                id_token, clock_skew_seconds=_CLOCK_SKEW_SECONDS
            )
        except firebase_auth_admin.ExpiredIdTokenError:
            return (
                jsonify(
                    {"error": "Your session expired.", "code": "token_expired"}
                ),
                401,
            )
        except firebase_auth_admin.RevokedIdTokenError:
            return (
                jsonify(
                    {
                        "error": "Your session was revoked. Please sign in again.",
                        "code": "unauthenticated",
                    }
                ),
                401,
            )
        except Exception as exc:  # noqa: BLE001 - any verification failure is a 401
            # Log for the operator; never echo the exception to the client, since
            # it can contain token internals.
            logger.warning("ID token verification failed: %s", exc)
            return (
                jsonify(
                    {
                        "error": "Sign-in could not be verified. Please sign in again.",
                        "code": "unauthenticated",
                    }
                ),
                401,
            )

        g.uid = decoded["uid"]
        g.user_email = decoded.get("email")
        g.user_name = decoded.get("name")
        return f(*args, **kwargs)

    return wrapper
