"""Firestore access layer.

Every read and write goes through a function here, and every function takes a
`uid` and scopes itself to `users/{uid}/analyses/...`. Concentrating that in one
module means there is exactly one place to audit for the question "can a user
reach someone else's data?", instead of a path built ad hoc in each route.

The client is created lazily on first use - same credential story as
firebase_auth.py: automatic on Cloud Run, GOOGLE_APPLICATION_CREDENTIALS locally.
"""
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

import config

_db: Optional[firestore.Client] = None

ANALYSES = "analyses"
USERS = "users"


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _analyses(uid: str):
    return get_db().collection(USERS).document(uid).collection(ANALYSES)


def _serialise(doc) -> dict:
    """Firestore document -> JSON-safe dict, with timestamps as ISO strings."""
    item = doc.to_dict() or {}
    item["id"] = doc.id
    for field in ("createdAt", "updatedAt"):
        value = item.get(field)
        if isinstance(value, datetime):
            item[field] = value.isoformat()
        elif value is not None:
            # Firestore may hand back its own timestamp type depending on SDK
            # version; str() is always safe and always JSON-serialisable.
            item[field] = str(value)
    return item


def create_analysis(uid: str, *, meta: dict, result: dict, resume_text: str, jd_text: str) -> dict:
    """Persist one completed analysis. Returns the saved record.

    `resume_text` and `jd_text` are stored so a saved analysis can be reopened
    and re-compared later. That is a real privacy tradeoff, so it is disclosed in
    the UI and every record is deletable by its owner in one click.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "company": meta["company"],
        "role": meta["role"],
        "location": meta.get("location", ""),
        "resumeText": resume_text,
        "jdText": jd_text,
        "result": result,
        # Denormalised for cheap list rendering and sorting without opening
        # every full document.
        "fitScore": result.get("fit_score", 0),
        "verdict": result.get("verdict", ""),
        "status": "saved",
        "notes": "",
        "deadline": "",
        "createdAt": now,
        "updatedAt": now,
    }
    doc_ref = _analyses(uid).document()
    doc_ref.set(payload)

    saved = dict(payload)
    saved["id"] = doc_ref.id
    saved["createdAt"] = now.isoformat()
    saved["updatedAt"] = now.isoformat()
    return saved


def list_analyses(uid: str, limit: int = config.HISTORY_PAGE_SIZE) -> list:
    """History list. Deliberately omits resumeText/jdText and the full result
    blob - the list view needs neither, and shipping them would make the page
    heavy and leak more data than the view requires."""
    query = (
        _analyses(uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    items = []
    for doc in query.stream():
        full = _serialise(doc)
        result = full.get("result") or {}
        readiness = result.get("readiness") or {}
        items.append(
            {
                "id": full["id"],
                "company": full.get("company", ""),
                "role": full.get("role", ""),
                "location": full.get("location", ""),
                "fitScore": full.get("fitScore", 0),
                "verdict": full.get("verdict", ""),
                "status": full.get("status", "saved"),
                "notes": full.get("notes", ""),
                "deadline": full.get("deadline", ""),
                "createdAt": full.get("createdAt"),
                "updatedAt": full.get("updatedAt"),
                "criticalGaps": sum(
                    1
                    for gap in (result.get("gaps") or [])
                    if gap.get("severity") == "critical"
                ),
                "evidenceVerified": result.get("evidence_verified_count", 0),
                "evidenceTotal": result.get("evidence_total_count", 0),
                "readinessScores": {
                    key: (readiness.get(key) or {}).get("score", 0)
                    for key in (
                        "skill_fit",
                        "evidence_strength",
                        "keyword_alignment",
                        "application_completeness",
                    )
                },
            }
        )
    return items


def get_analysis(uid: str, analysis_id: str) -> Optional[dict]:
    """Fetch one record. Because the path is built from the verified uid, a user
    asking for another user's document ID simply gets None."""
    doc = _analyses(uid).document(analysis_id).get()
    if not doc.exists:
        return None
    return _serialise(doc)


def get_analyses_by_ids(uid: str, ids: list) -> list:
    """Batch fetch for the compare feature, preserving the requested order."""
    refs = [_analyses(uid).document(doc_id) for doc_id in ids]
    found = {doc.id: _serialise(doc) for doc in get_db().get_all(refs) if doc.exists}
    return [found[doc_id] for doc_id in ids if doc_id in found]


def update_analysis(uid: str, analysis_id: str, updates: dict) -> Optional[dict]:
    """Apply an already-validated whitelist of field updates."""
    doc_ref = _analyses(uid).document(analysis_id)
    if not doc_ref.get().exists:
        return None
    payload = dict(updates)
    payload["updatedAt"] = datetime.now(timezone.utc)
    doc_ref.update(payload)
    return _serialise(doc_ref.get())


def attach_plan(uid: str, analysis_id: str, plan: dict) -> bool:
    """Merge the second-stage action plan into an already-saved analysis.

    Read-modify-write rather than a dotted field update, because the plan keys
    are nested inside `result` alongside the assessment and we want the two
    halves to end up in one coherent document.
    """
    doc_ref = _analyses(uid).document(analysis_id)
    snapshot = doc_ref.get()
    if not snapshot.exists:
        return False

    result = (snapshot.to_dict() or {}).get("result") or {}
    result.update(plan)
    doc_ref.update({"result": result, "updatedAt": datetime.now(timezone.utc)})
    return True


def delete_analysis(uid: str, analysis_id: str) -> bool:
    """Hard delete - the record is gone, not flagged. That is what a Delete
    button should mean."""
    doc_ref = _analyses(uid).document(analysis_id)
    if not doc_ref.get().exists:
        return False
    doc_ref.delete()
    return True
