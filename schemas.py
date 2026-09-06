"""Typed contracts for everything that crosses a trust boundary.

Two directions:
  1. Gemini -> us.  The *AI response models* are handed to the Gemini SDK as a
     response_schema, so the model is constrained at generation time. We still
     re-validate and normalise on arrival, because a constrained decode is a
     strong guarantee about *shape*, not about *values* (an enum-ish field can
     still come back as "Critical" or "must-have").
  2. Browser -> us.  The request validators reject anything oversized, empty,
     or of the wrong type before it reaches Gemini or Firestore.

Deliberate schema choices for SDK compatibility: plain str/int/List only - no
Optional, no Union, no Literal, no defaults. Those convert unreliably to the
OpenAPI subset Gemini accepts. Value constraints are enforced in the prompt and
then repaired here.
"""
from typing import Any, List

from pydantic import BaseModel, Field

import config

# ---------------------------------------------------------------------------
# 1. AI response models
# ---------------------------------------------------------------------------


class SkillEvidence(BaseModel):
    """One JD requirement matched to a VERBATIM quote from the resume.

    This is the backbone of the product's honesty claim: a matched skill with
    no quotable evidence is not a match, it is a guess.
    """

    skill: str = Field(description="The skill or requirement, 2-6 words")
    jd_requirement: str = Field(
        description="The job-description phrase this skill comes from, quoted or closely paraphrased"
    )
    resume_quote: str = Field(
        description=(
            "VERBATIM text copied from the resume that proves this skill. "
            "Copy it exactly, character for character. If no exact supporting "
            "text exists, do not include this skill at all."
        )
    )
    strength: str = Field(description="Exactly one of: strong, moderate, weak")
    explanation: str = Field(
        description="One sentence on how the quote satisfies the requirement"
    )


class SkillGap(BaseModel):
    skill: str = Field(description="The missing or under-evidenced skill")
    jd_requirement: str = Field(description="The JD phrase that asks for it")
    severity: str = Field(
        description=(
            "Exactly one of: critical, important, nice_to_have. "
            "critical = the JD lists it as required and the resume shows nothing; "
            "important = required but only partially covered; "
            "nice_to_have = listed as preferred or bonus."
        )
    )
    why_it_matters: str = Field(description="One sentence on the risk this gap creates")
    fastest_credible_proof: str = Field(
        description="The most realistic way to genuinely show this skill within a week"
    )


class ReadinessDimension(BaseModel):
    score: int = Field(description="0 to 100")
    note: str = Field(description="One short sentence justifying this score")


class Readiness(BaseModel):
    """Four independent axes. Deliberately NOT a hiring prediction - each axis
    measures something the applicant can actually change before applying."""

    skill_fit: ReadinessDimension = Field(
        description="How well the candidate's real skills cover the JD requirements"
    )
    evidence_strength: ReadinessDimension = Field(
        description="How concrete and quantified the resume proof is: metrics, scope, outcomes"
    )
    keyword_alignment: ReadinessDimension = Field(
        description="How well resume vocabulary matches the terminology the JD itself uses"
    )
    application_completeness: ReadinessDimension = Field(
        description="Whether the resume covers everything the JD explicitly asks applicants to show"
    )


class BulletRewrite(BaseModel):
    before: str = Field(
        description="The EXACT original bullet, copied verbatim from the resume"
    )
    after: str = Field(
        description=(
            "The rewritten bullet. Use ONLY facts present in the original. Never "
            "add technologies, metrics, scope or seniority the resume does not state."
        )
    )
    jd_requirement: str = Field(
        description="The specific JD requirement this edit supports"
    )
    rationale: str = Field(description="One sentence on what the edit improves and why")


class TruthFlag(BaseModel):
    """The ethical differentiator: claims the JD tempts the user to make that
    their resume does not actually support."""

    tempting_claim: str = Field(
        description="A claim the JD invites but the resume does not support"
    )
    why_unsupported: str = Field(description="Why the resume does not back this up")
    honest_alternative: str = Field(
        description=(
            "A truthful way to express adjacent real experience, or how to "
            "genuinely earn the right to make this claim"
        )
    )


class CoreAnalysis(BaseModel):
    """First Gemini call: the evidence-grounded assessment."""

    fit_score: int = Field(
        description="Overall preparation fit, 0 to 100. Be honest; do not inflate."
    )
    verdict: str = Field(description="One punchy sentence summarising the fit")
    score_rationale: str = Field(
        description=(
            "2-4 sentences explaining exactly how this score was reached: what "
            "pushed it up and what held it back. Reference specific requirements."
        )
    )
    readiness: Readiness
    matched_skills: List[SkillEvidence] = Field(
        description="4 to 8 evidence-backed matches, strongest first"
    )
    gaps: List[SkillGap] = Field(description="3 to 6 gaps, most severe first")
    missing_keywords: List[str] = Field(
        description="5 to 12 exact terms the JD uses that the resume never states"
    )
    bullet_rewrites: List[BulletRewrite] = Field(description="3 to 5 rewrites")
    truth_guard: List[TruthFlag] = Field(description="2 to 4 flags")
    positioning_summary: str = Field(
        description=(
            "3-4 sentence honest positioning paragraph the user could adapt for "
            "a cover letter, grounded only in real resume content"
        )
    )


class PlanDay(BaseModel):
    day: int = Field(description="1 to 7")
    focus: str = Field(description="The single gap this day closes, 3-8 words")
    tasks: List[str] = Field(description="2 to 4 concrete tasks")
    deliverable: str = Field(
        description=(
            "Something that exists at the end of the day and could honestly be "
            "linked or described on the resume"
        )
    )
    estimated_minutes: int = Field(description="Realistic total minutes, 60 to 240")


class InterviewQuestion(BaseModel):
    question: str = Field(description="A question this JD makes likely")
    why_asked: str = Field(description="Which JD requirement motivates it")
    talking_points: List[str] = Field(
        description=(
            "2 to 4 hints drawn ONLY from the candidate's actual resume. Never "
            "suggest they claim experience the resume does not show."
        )
    )
    resume_anchor: str = Field(
        description="The resume experience these points build on, quoted or closely paraphrased"
    )


class ActionPlan(BaseModel):
    """Second Gemini call: what to actually do next."""

    plan_summary: str = Field(description="One sentence on what this week achieves")
    seven_day_plan: List[PlanDay] = Field(
        description="Exactly 7 entries, day 1 through day 7"
    )
    interview_questions: List[InterviewQuestion] = Field(
        description="Exactly 5 questions"
    )
    quick_wins: List[str] = Field(
        description=(
            "3 changes that each take under 15 minutes and measurably improve "
            "the application"
        )
    )


class RoleComparison(BaseModel):
    """Third call, used only by the compare-roles feature."""

    best_fit_role: str = Field(
        description="Role at Company of the strongest option, formatted exactly that way"
    )
    best_fit_reason: str = Field(description="2-3 sentences on why it wins")
    recommended_order: List[str] = Field(
        description="All compared roles as Role at Company, best first"
    )
    tradeoffs: List[str] = Field(
        description="3 to 5 honest tradeoffs between the options"
    )
    shared_gaps: List[str] = Field(
        description="Skills missing across multiple roles - the highest-leverage things to fix"
    )


# ---------------------------------------------------------------------------
# 2. Normalisers - repair plausible-but-off values from the model
# ---------------------------------------------------------------------------

_STRENGTHS = {"strong", "moderate", "weak"}
_SEVERITIES = {"critical", "important", "nice_to_have"}

_SEVERITY_ALIASES = {
    "must_have": "critical",
    "must have": "critical",
    "required": "critical",
    "blocker": "critical",
    "high": "critical",
    "medium": "important",
    "moderate": "important",
    "preferred": "nice_to_have",
    "nice_to_have": "nice_to_have",
    "nice to have": "nice_to_have",
    "optional": "nice_to_have",
    "bonus": "nice_to_have",
    "low": "nice_to_have",
}

_STRENGTH_ALIASES = {
    "high": "strong",
    "strong_match": "strong",
    "medium": "moderate",
    "partial": "moderate",
    "low": "weak",
    "implied": "weak",
    "indirect": "weak",
}

SEVERITY_RANK = {"critical": 0, "important": 1, "nice_to_have": 2}


def _clamp(value: Any, low: int = 0, high: int = 100) -> int:
    try:
        return max(low, min(high, int(round(float(value)))))
    except (TypeError, ValueError):
        return low


def _normalise_token(raw: Any, allowed: set, aliases: dict, fallback: str) -> str:
    token = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    if token in allowed:
        return token
    spaced = token.replace("_", " ")
    return aliases.get(token) or aliases.get(spaced) or fallback


def normalise_core(core: CoreAnalysis) -> dict:
    """Coerce a validated CoreAnalysis into the exact shape the UI renders."""
    data = core.model_dump()
    data["fit_score"] = _clamp(data.get("fit_score"))

    readiness = data.get("readiness") or {}
    for dim in (
        "skill_fit",
        "evidence_strength",
        "keyword_alignment",
        "application_completeness",
    ):
        block = readiness.get(dim) or {}
        block["score"] = _clamp(block.get("score"))
        block["note"] = str(block.get("note") or "").strip()
        readiness[dim] = block
    data["readiness"] = readiness

    for match in data.get("matched_skills", []):
        match["strength"] = _normalise_token(
            match.get("strength"), _STRENGTHS, _STRENGTH_ALIASES, "moderate"
        )

    for gap in data.get("gaps", []):
        gap["severity"] = _normalise_token(
            gap.get("severity"), _SEVERITIES, _SEVERITY_ALIASES, "important"
        )
    data["gaps"] = sorted(
        data.get("gaps", []), key=lambda gap: SEVERITY_RANK[gap["severity"]]
    )

    # De-duplicate keywords case-insensitively, preserving the model ordering.
    seen: set = set()
    keywords: list = []
    for word in data.get("missing_keywords", []):
        cleaned = str(word).strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            keywords.append(cleaned)
    data["missing_keywords"] = keywords[:12]
    return data


def normalise_plan(plan: ActionPlan) -> dict:
    data = plan.model_dump()
    days = []
    for index, day in enumerate(data.get("seven_day_plan", [])[:7], start=1):
        day["day"] = index  # authoritative ordering, whatever the model numbered them
        day["estimated_minutes"] = _clamp(day.get("estimated_minutes"), 15, 480)
        days.append(day)
    data["seven_day_plan"] = days
    data["interview_questions"] = data.get("interview_questions", [])[:5]
    data["quick_wins"] = data.get("quick_wins", [])[:5]
    return data


def verify_quotes(core: dict, resume_text: str) -> dict:
    """Post-hoc grounding check - the mechanism behind 'proof-backed fit'.

    Every resume_quote and every rewrite `before` is checked against the real
    resume text. Unverified quotes are not deleted, they are LABELLED, so the UI
    can show the user exactly which claims the model could not ground. Whitespace
    is collapsed before comparison because PDF extraction and model output
    disagree about line breaks constantly.
    """
    haystack = " ".join(resume_text.split()).lower()

    def grounded(quote: Any) -> bool:
        needle = " ".join(str(quote or "").split()).lower()
        if len(needle) < 12:
            return False
        if needle in haystack:
            return True
        # Tolerate a clipped tail - models often truncate a quote mid-phrase.
        return len(needle) > 40 and needle[:40] in haystack

    verified_count = 0
    for match in core.get("matched_skills", []):
        match["verified"] = grounded(match.get("resume_quote"))
        verified_count += bool(match["verified"])
    for rewrite in core.get("bullet_rewrites", []):
        rewrite["verified"] = grounded(rewrite.get("before"))

    total = len(core.get("matched_skills", []))
    core["evidence_verified_count"] = verified_count
    core["evidence_total_count"] = total
    core["evidence_verified_pct"] = round(100 * verified_count / total) if total else 0
    return core


MIN_REQUIREMENT_CHARS = 10


def _normalised_index(text: str):
    """Collapse whitespace while remembering where each surviving character came
    from in the original string.

    Returns (haystack, index_map) where haystack is lowercased with runs of
    whitespace collapsed to one space, and index_map[i] is the offset in the
    ORIGINAL text of haystack[i]. That lets us search tolerantly but still
    highlight exact spans of the untouched source.
    """
    chars: list = []
    index_map: list = []
    previous_was_space = False
    for offset, char in enumerate(text):
        if char.isspace():
            if previous_was_space:
                continue
            chars.append(" ")
            index_map.append(offset)
            previous_was_space = True
        else:
            chars.append(char.lower())
            index_map.append(offset)
            previous_was_space = False
    return "".join(chars), index_map


def build_jd_coverage(core: dict, jd_text: str) -> dict:
    """Map the analysis back onto the job description itself.

    Each requirement the model cited is located in the original posting and
    tagged as covered (evidence found in the resume) or a gap. The result is a
    flat list of segments the UI renders as highlighted text, which turns the
    abstract claim "you match 5 of 8 requirements" into something the user can
    literally see on the posting they are about to apply to.

    Requirements the model paraphrased rather than quoted simply will not be
    found, and are skipped rather than guessed at.
    """
    haystack, index_map = _normalised_index(jd_text)

    requirements = [
        (match.get("jd_requirement"), "matched")
        for match in core.get("matched_skills", [])
    ] + [
        (gap.get("jd_requirement"), "gap") for gap in core.get("gaps", [])
    ]

    spans: list = []
    located = {"matched": 0, "gap": 0}
    for raw, kind in requirements:
        needle, _ = _normalised_index(str(raw or "").strip())
        if len(needle) < MIN_REQUIREMENT_CHARS:
            continue
        position = haystack.find(needle)
        if position < 0:
            continue
        start = index_map[position]
        end = index_map[position + len(needle) - 1] + 1
        # Matched requirements are appended first, so on an overlap the
        # positive evidence wins and the duplicate gap span is dropped.
        if any(start < other_end and end > other_start for other_start, other_end, _ in spans):
            continue
        spans.append((start, end, kind))
        located[kind] += 1

    spans.sort(key=lambda span: span[0])

    segments: list = []
    cursor = 0
    for start, end, kind in spans:
        if start > cursor:
            segments.append({"text": jd_text[cursor:start], "kind": "plain"})
        segments.append({"text": jd_text[start:end], "kind": kind})
        cursor = end
    if cursor < len(jd_text):
        segments.append({"text": jd_text[cursor:], "kind": "plain"})

    total = located["matched"] + located["gap"]
    return {
        "segments": segments,
        "matched_count": located["matched"],
        "gap_count": located["gap"],
        "located_count": total,
        "requirement_count": len(requirements),
        "covered_pct": round(100 * located["matched"] / total) if total else 0,
    }


# ---------------------------------------------------------------------------
# 3. Request validation
# ---------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised for bad client input. The message is safe to show the user."""


def clean_text(raw: Any, field: str, *, max_chars: int, min_chars: int = 0) -> str:
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise ValidationError(field + " must be text.")
    value = raw.strip()
    if min_chars and len(value) < min_chars:
        raise ValidationError(
            "{} needs at least {} characters to analyse (got {}). "
            "Paste the full text.".format(field, min_chars, len(value))
        )
    if len(value) > max_chars:
        raise ValidationError(
            "{} is too long ({} characters, limit {}). Trim it to the most "
            "relevant sections.".format(field, len(value), max_chars)
        )
    return value


def validate_analyze_request(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    return {
        "resume_text": clean_text(
            payload.get("resume_text"),
            "Resume",
            max_chars=config.MAX_RESUME_CHARS,
            min_chars=config.MIN_RESUME_CHARS,
        ),
        "jd_text": clean_text(
            payload.get("jd_text"),
            "Job description",
            max_chars=config.MAX_JD_CHARS,
            min_chars=config.MIN_JD_CHARS,
        ),
        "company": clean_text(
            payload.get("company"), "Company", max_chars=config.MAX_SHORT_FIELD_CHARS
        )
        or "Unspecified company",
        "role": clean_text(
            payload.get("role"), "Role", max_chars=config.MAX_SHORT_FIELD_CHARS
        )
        or "Unspecified role",
        "location": clean_text(
            payload.get("location"), "Location", max_chars=config.MAX_SHORT_FIELD_CHARS
        ),
    }


def validate_update_request(payload: Any) -> dict:
    """Whitelist of client-mutable fields. Anything else is ignored outright, so
    the client can never touch scores, evidence, or ownership."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    updates: dict = {}
    if "status" in payload:
        status = str(payload.get("status") or "").strip().lower()
        if status not in config.ALLOWED_STATUSES:
            raise ValidationError(
                "Status must be one of: " + ", ".join(config.ALLOWED_STATUSES)
            )
        updates["status"] = status
    if "notes" in payload:
        updates["notes"] = clean_text(
            payload.get("notes"), "Notes", max_chars=config.MAX_NOTES_CHARS
        )
    if "deadline" in payload:
        deadline = str(payload.get("deadline") or "").strip()
        if deadline:
            from datetime import date

            try:
                date.fromisoformat(deadline)
            except ValueError:
                raise ValidationError("Deadline must be a date in YYYY-MM-DD format.")
        updates["deadline"] = deadline

    if not updates:
        raise ValidationError("Nothing to update. Send status, notes, or deadline.")
    return updates


def validate_compare_request(payload: Any) -> list:
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    ids = payload.get("ids")
    if not isinstance(ids, list):
        raise ValidationError("Send an 'ids' array of saved analysis IDs.")

    cleaned: list = []
    for item in ids:
        doc_id = str(item or "").strip()
        # Firestore IDs are opaque; reject anything path-like or absurd before it
        # is ever used to build a document reference.
        if not doc_id or "/" in doc_id or len(doc_id) > 128:
            raise ValidationError("One of the supplied IDs is not valid.")
        if doc_id not in cleaned:
            cleaned.append(doc_id)

    if not 2 <= len(cleaned) <= config.MAX_COMPARE_ITEMS:
        raise ValidationError(
            "Select between 2 and {} saved analyses to compare.".format(
                config.MAX_COMPARE_ITEMS
            )
        )
    return cleaned
