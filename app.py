"""RoleFit - Flask application and JSON API.

Route map
---------
GET    /                      dashboard shell (auth handled client-side)
GET    /healthz               dependency-free liveness probe (local)
GET    /api/health            same probe; use this one on *.run.app
GET    /api/config            public Firebase web config (no secrets)
POST   /api/extract-resume    PDF  -> plain text                    [auth]
POST   /api/analyze           resume + JD -> assessment, saved      [auth]
POST   /api/analyses/<id>/plan  stage two: 7-day plan + interview     [auth]
GET    /api/analyses          history list (summaries only)         [auth]
GET    /api/analyses/<id>     one full analysis                     [auth]
PATCH  /api/analyses/<id>     status / notes / deadline             [auth]
DELETE /api/analyses/<id>     permanent delete                      [auth]
POST   /api/compare           compare 2-3 saved analyses            [auth]

Every [auth] route is wrapped in @login_required, which verifies the Firebase ID
token server-side, and every data access is scoped by the uid that verification
returns - never by an ID supplied in the request body.
"""
import logging
import os

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

import config
import firestore_client as store
import gemini_client
import schemas
from firebase_auth import login_required
from resume_parser import ExtractionError, extract_text_from_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rolefit")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
app.config["JSON_SORT_KEYS"] = False

for _problem in config.missing_required():
    logger.warning("CONFIG: %s", _problem)


# ---------------------------------------------------------------------------
# Cross-cutting concerns
# ---------------------------------------------------------------------------


@app.after_request
def security_headers(response):
    """Baseline hardening. The CSP allows exactly the Google origins the
    Firebase SDK and sign-in popup need, and nothing else."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    if request.path.startswith("/api/"):
        # Analyses contain personal data; never let a proxy or the browser cache them.
        response.headers.setdefault("Cache-Control", "no-store")
    else:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://www.gstatic.com https://apis.google.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://*.googleapis.com https://*.firebaseio.com "
            "https://securetoken.googleapis.com https://identitytoolkit.googleapis.com; "
            "frame-src https://*.firebaseapp.com https://accounts.google.com; "
            "base-uri 'self'; form-action 'self'",
        )
    return response


def fail(message: str, status: int, code: str = ""):
    body = {"error": message}
    if code:
        body["code"] = code
    return jsonify(body), status


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_exc):
    limit_mb = config.MAX_CONTENT_LENGTH / (1024 * 1024)
    return fail(
        "That request is too large (limit {:.0f} MB). Try a smaller file or "
        "shorter text.".format(limit_mb),
        413,
        "too_large",
    )


@app.errorhandler(404)
def handle_404(_exc):
    if request.path.startswith("/api/"):
        return fail("That endpoint does not exist.", 404, "not_found")
    return render_template("index.html"), 404


@app.errorhandler(500)
def handle_500(exc):
    logger.exception("Unhandled server error: %s", exc)
    return fail("Something went wrong on our side. Please try again.", 500, "server_error")


def json_body():
    """Parse a JSON body without letting a malformed one raise a 400 HTML page."""
    return request.get_json(force=True, silent=True)


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
@app.route("/api/health")
def healthz():
    """No dependencies on purpose: confirms the container is serving even when
    Firestore or Gemini are misconfigured, which is what makes a bad deploy
    diagnosable rather than just 'down'.

    Exposed at two paths because Google's frontend intercepts /healthz on a
    *.run.app domain and answers it with its own 404 before the request ever
    reaches the container. /api/health is never intercepted, so it is the one
    to use against a deployed service; /healthz still works everywhere else.
    """
    return jsonify({"status": "ok"}), 200


@app.route("/api/config")
def api_config():
    """Firebase web config for the browser.

    These values are public by design - the Firebase JS SDK ships them to every
    client. Serving them from env keeps them out of source control and lets one
    image target dev and prod. No secret is ever included here.
    """
    if not config.FIREBASE_WEB_CONFIG.get("apiKey"):
        return fail(
            "Sign-in is not configured on this server. The administrator needs "
            "to set the FIREBASE_* environment variables.",
            503,
            "not_configured",
        )
    return jsonify(
        {
            "firebase": config.FIREBASE_WEB_CONFIG,
            "limits": {
                "maxResumeChars": config.MAX_RESUME_CHARS,
                "maxJdChars": config.MAX_JD_CHARS,
                "minResumeChars": config.MIN_RESUME_CHARS,
                "minJdChars": config.MIN_JD_CHARS,
                "maxUploadBytes": config.MAX_UPLOAD_BYTES,
                "maxCompareItems": config.MAX_COMPARE_ITEMS,
            },
            "statuses": list(config.ALLOWED_STATUSES),
        }
    )


# ---------------------------------------------------------------------------
# Authenticated API
# ---------------------------------------------------------------------------


@app.route("/api/extract-resume", methods=["POST"])
@login_required
def api_extract_resume():
    if "file" not in request.files:
        return fail("No file was uploaded.", 400, "no_file")
    try:
        text = extract_text_from_pdf(request.files["file"])
    except ExtractionError as exc:
        return fail(str(exc), 400, "bad_upload")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected extraction failure: %s", exc)
        return fail("That file could not be processed.", 500, "server_error")

    return jsonify({"text": text, "charCount": len(text)})


@app.route("/api/analyze", methods=["POST"])
@login_required
def api_analyze():
    try:
        payload = schemas.validate_analyze_request(json_body())
    except schemas.ValidationError as exc:
        return fail(str(exc), 400, "invalid_input")

    try:
        result = gemini_client.analyze_core(
            payload["resume_text"], payload["jd_text"], payload["company"], payload["role"]
        )
    except gemini_client.AnalysisError as exc:
        # Already phrased for a user; no internal detail leaks.
        return fail(str(exc), 502, "analysis_failed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analysis crashed: %s", exc)
        return fail(
            "The analysis service is unavailable right now. Please try again.",
            502,
            "analysis_failed",
        )

    try:
        saved = store.create_analysis(
            g.uid,
            meta={
                "company": payload["company"],
                "role": payload["role"],
                "location": payload["location"],
            },
            result=result,
            resume_text=payload["resume_text"],
            jd_text=payload["jd_text"],
        )
    except Exception as exc:  # noqa: BLE001
        # The analysis succeeded and is expensive - return it rather than losing
        # the user's work to a storage hiccup, but tell them it was not saved.
        logger.exception("Firestore save failed: %s", exc)
        return jsonify(
            {
                "id": None,
                "saved": False,
                "saveError": "Your analysis is ready but could not be saved to your history.",
                "result": result,
                "company": payload["company"],
                "role": payload["role"],
                "location": payload["location"],
            }
        )

    logger.info("Assessment saved uid=%s id=%s score=%s", g.uid, saved["id"], saved["fitScore"])
    return jsonify(
        {
            "id": saved["id"],
            "saved": True,
            "result": result,
            "company": saved["company"],
            "role": saved["role"],
            "location": saved["location"],
            "status": saved["status"],
            "createdAt": saved["createdAt"],
        }
    )


PLAN_FIELDS = ("plan_summary", "seven_day_plan", "interview_questions", "quick_wins")


@app.route("/api/analyses/<analysis_id>/plan", methods=["POST"])
@login_required
def api_generate_plan(analysis_id):
    """Stage two of the analysis.

    Split from /api/analyze so the browser can paint the assessment as soon as
    it arrives rather than holding a spinner until the plan is also done. The
    stored resume and job description are re-read from the user's own document,
    so this route takes no content from the request body at all.
    """
    record = store.get_analysis(g.uid, analysis_id)
    if record is None:
        return fail("That analysis was not found.", 404, "not_found")

    result = record.get("result") or {}
    if result.get("seven_day_plan"):
        # Already generated - return it instead of spending another Gemini call.
        return jsonify(
            {"plan": {key: result.get(key) for key in PLAN_FIELDS}, "cached": True}
        )

    try:
        plan = gemini_client.generate_plan(
            record.get("resumeText", ""),
            record.get("jdText", ""),
            record.get("company", ""),
            record.get("role", ""),
            core=result,
        )
    except gemini_client.AnalysisError as exc:
        return fail(str(exc), 502, "analysis_failed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Plan generation crashed: %s", exc)
        return fail(
            "The action plan could not be generated. Your assessment is saved - "
            "you can retry the plan.",
            502,
            "analysis_failed",
        )

    if not store.attach_plan(g.uid, analysis_id, plan):
        # The record vanished mid-flight; the plan is still useful to return.
        logger.warning("Plan generated for a missing record uid=%s id=%s", g.uid, analysis_id)

    logger.info("Plan generated uid=%s id=%s", g.uid, analysis_id)
    return jsonify({"plan": plan, "cached": False})


@app.route("/api/analyses", methods=["GET"])
@login_required
def api_list_analyses():
    try:
        limit = min(int(request.args.get("limit", config.HISTORY_PAGE_SIZE)), 100)
    except (TypeError, ValueError):
        limit = config.HISTORY_PAGE_SIZE

    try:
        items = store.list_analyses(g.uid, limit=max(1, limit))
    except Exception as exc:  # noqa: BLE001
        logger.exception("History query failed: %s", exc)
        return fail("Your history could not be loaded. Please try again.", 503, "store_error")
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/analyses/<analysis_id>", methods=["GET"])
@login_required
def api_get_analysis(analysis_id):
    record = store.get_analysis(g.uid, analysis_id)
    if record is None:
        return fail("That analysis was not found.", 404, "not_found")
    return jsonify(record)


@app.route("/api/analyses/<analysis_id>", methods=["PATCH"])
@login_required
def api_update_analysis(analysis_id):
    try:
        updates = schemas.validate_update_request(json_body())
    except schemas.ValidationError as exc:
        return fail(str(exc), 400, "invalid_input")

    record = store.update_analysis(g.uid, analysis_id, updates)
    if record is None:
        return fail("That analysis was not found.", 404, "not_found")
    return jsonify(
        {
            "id": record["id"],
            "status": record.get("status"),
            "notes": record.get("notes"),
            "deadline": record.get("deadline"),
            "updatedAt": record.get("updatedAt"),
        }
    )


@app.route("/api/analyses/<analysis_id>", methods=["DELETE"])
@login_required
def api_delete_analysis(analysis_id):
    if not store.delete_analysis(g.uid, analysis_id):
        return fail("That analysis was not found.", 404, "not_found")
    logger.info("Analysis deleted uid=%s id=%s", g.uid, analysis_id)
    return jsonify({"deleted": True, "id": analysis_id})


@app.route("/api/compare", methods=["POST"])
@login_required
def api_compare():
    try:
        ids = schemas.validate_compare_request(json_body())
    except schemas.ValidationError as exc:
        return fail(str(exc), 400, "invalid_input")

    items = store.get_analyses_by_ids(g.uid, ids)
    if len(items) < 2:
        return fail(
            "At least two of the selected analyses could not be found.", 404, "not_found"
        )

    try:
        comparison = gemini_client.compare_applications(items)
    except gemini_client.AnalysisError as exc:
        return fail(str(exc), 502, "analysis_failed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Comparison crashed: %s", exc)
        return fail("The comparison could not be generated. Please try again.", 502, "analysis_failed")

    return jsonify(
        {
            "comparison": comparison,
            "roles": [
                {
                    "id": item["id"],
                    "company": item.get("company", ""),
                    "role": item.get("role", ""),
                    "fitScore": item.get("fitScore", 0),
                    "readiness": (item.get("result") or {}).get("readiness", {}),
                }
                for item in items
            ],
        }
    )


if __name__ == "__main__":
    # Local development only. Cloud Run runs gunicorn (see Dockerfile), which
    # never executes this block - so debug can never leak into production.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", config.PORT)), debug=config.FLASK_DEBUG)
