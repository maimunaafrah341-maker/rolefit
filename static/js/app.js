/* ==========================================================================
   RoleFit client
   - Firebase config is fetched from /api/config at boot, never hard-coded.
   - Every API call attaches a fresh Firebase ID token; a 401 with code
     token_expired triggers one forced refresh and a single retry, so a long
     session never dumps the user back to sign-in mid-demo.
   - Analysis arrives in two stages: /api/analyze returns the assessment and
     paints immediately, then the 7-day plan and interview questions stream
     into reserved slots. The user starts reading in ~12s instead of ~30s.
   - All dynamic text goes through esc(). Analysis output is model-generated
     and resumes are user-supplied: neither is ever trusted as markup.
   ========================================================================== */

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

import { SAMPLE } from "./sample-data.js";

/* ---------- tiny DOM helpers ---------- */
const $ = (id) => document.getElementById(id);
const show = (el) => el && el.classList.remove("hidden");
const hide = (el) => el && el.classList.add("hidden");
const toggle = (el, on) => el && el.classList.toggle("hidden", !on);

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const DROPZONE_LABEL = "📄 Drop a PDF here or click to upload · max 5 MB";

const state = {
  auth: null, user: null, limits: {}, statuses: [],
  current: null,          // { id, company, role, result }
  pendingPlanFor: null,   // id whose stage-two plan is in flight
  history: [],
  compareSelection: new Set(),
};

/* ---------- toasts ---------- */
function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = "toast " + kind;
  node.textContent = message;
  $("toasts").appendChild(node);
  setTimeout(() => {
    node.style.transition = "opacity .3s ease";
    node.style.opacity = "0";
    setTimeout(() => node.remove(), 320);
  }, 4200);
}

/* ---------- confirm modal (never window.confirm — it looks broken on stage) ---------- */
function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const root = $("modal-root");
    root.innerHTML = `
      <div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div class="modal">
          <h3 id="modal-title">${esc(title)}</h3>
          <p class="muted" style="margin:.6rem 0 1.3rem;font-size:.9rem">${esc(body)}</p>
          <div class="row" style="justify-content:flex-end">
            <button class="btn" data-act="cancel">Cancel</button>
            <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-act="ok">${esc(confirmLabel)}</button>
          </div>
        </div>
      </div>`;

    const close = (result) => { root.innerHTML = ""; resolve(result); };
    root.querySelector('[data-act="cancel"]').onclick = () => close(false);
    root.querySelector('[data-act="ok"]').onclick = () => close(true);
    root.querySelector(".modal-backdrop").onclick = (e) => {
      if (e.target.classList.contains("modal-backdrop")) close(false);
    };
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") { document.removeEventListener("keydown", onKey); close(false); }
    });
    root.querySelector('[data-act="ok"]').focus();
  });
}

/* ---------- theme ---------- */
function initTheme() {
  const icon = $("theme-icon");
  const paint = () => { icon.textContent = document.documentElement.dataset.theme === "dark" ? "☀" : "☾"; };
  paint();
  $("theme-toggle").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("rolefit-theme", next); } catch (e) { /* private mode */ }
    paint();
  };
}

/* ---------- API ---------- */
class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}

async function api(path, { method = "GET", body, formData, retried = false } = {}) {
  if (!state.user) throw new ApiError("You are signed out.", 401, "unauthenticated");

  // forceRefresh on a retry: the first attempt failed because the cached token
  // had expired, so asking for the same cached token again would fail identically.
  const token = await state.user.getIdToken(retried);
  const headers = { Authorization: `Bearer ${token}` };
  const init = { method, headers };
  if (formData) {
    init.body = formData;                       // browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  const response = await fetch(path, init);
  let payload = {};
  try { payload = await response.json(); } catch (e) { /* non-JSON error page */ }

  if (!response.ok) {
    if (response.status === 401 && payload.code === "token_expired" && !retried) {
      return api(path, { method, body, formData, retried: true });
    }
    if (response.status === 401) {
      toast("Your session ended. Please sign in again.", "error");
      await signOut(state.auth);
    }
    throw new ApiError(payload.error || `Request failed (${response.status})`, response.status, payload.code);
  }
  return payload;
}

/* ---------- view + tab routing ---------- */
function setView(name) {
  ["boot", "config-error", "landing", "app"].forEach((v) => toggle($(`view-${v}`), v === name));
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  });
  ["analyze", "result", "history", "compare", "privacy"].forEach((panel) => {
    toggle($(`panel-${panel}`), panel === name);
  });
  if (name === "history" || name === "compare") loadHistory();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------- formatting ---------- */
const SEVERITY_META = {
  critical:     { label: "Critical",     cls: "badge-bad" },
  important:    { label: "Important",    cls: "badge-warn" },
  nice_to_have: { label: "Nice to have", cls: "badge-info" },
};
const STRENGTH_META = {
  strong:   { label: "Strong evidence",   cls: "badge-good" },
  moderate: { label: "Moderate evidence", cls: "badge-warn" },
  weak:     { label: "Weak evidence",     cls: "badge-info" },
};
const STATUS_LABELS = {
  saved: "Saved", applied: "Applied", assessment: "Assessment",
  interview: "Interview", rejected: "Rejected", offer: "Offer",
};

const scoreTone = (n) => (n >= 75 ? "good" : n >= 55 ? "warn" : "bad");
const scoreVar = (n) => `var(--${scoreTone(n)})`;

function formatDate(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (isNaN(date)) return "";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/* ==========================================================================
   Result rendering
   ========================================================================== */

function hasPlan(result) {
  return Array.isArray(result.seven_day_plan) && result.seven_day_plan.length > 0;
}

function pendingCard(title, subtitle) {
  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>${esc(title)}</h3>
        <div class="sub">${esc(subtitle)}</div>
      </div>
      <span class="badge badge-brand spacer-end"><span class="pulse-dot"></span> Generating</span>
    </div>
    <div class="stack" style="gap:.55rem">
      <div class="skeleton" style="width:70%"></div>
      <div class="skeleton" style="width:90%"></div>
      <div class="skeleton" style="width:55%"></div>
      <div class="skeleton" style="width:80%"></div>
    </div>
  </div>`;
}

function renderResult(record) {
  state.current = record;
  const r = record.result || {};
  const planReady = hasPlan(r);
  hide($("result-empty"));
  show($("result-body"));

  // The assessment paints immediately; the plan and interview cards occupy
  // reserved slots that stage two fills in without redrawing anything else.
  $("result-body").innerHTML = [
    scoreCard(record, r),
    readinessCard(r),
    evidenceCard(r),
    coverageCard(r),
    gapsCard(r),
    `<div id="plan-slot">${planReady ? planCard(r)
      : pendingCard("Your 7-day plan", "Building a week around the gaps we just found…")}</div>`,
    rewritesCard(r),
    truthGuardCard(r),
    `<div id="interview-slot">${planReady ? interviewCard(r)
      : pendingCard("Interview preparation", "Drafting questions grounded in your experience…")}</div>`,
    keywordsCard(r),
    manageCard(record),
  ].join("");

  // Animate the meters and ring after paint so the transition actually runs.
  requestAnimationFrame(() => {
    document.querySelectorAll("#result-body .meter-fill").forEach((el) => {
      el.style.width = `${el.dataset.value}%`;
    });
    const ring = document.querySelector("#result-body .ring");
    if (ring) ring.style.setProperty("--pct", ring.dataset.value);
  });

  wireResultActions(record);

  if (!planReady && record.id) fetchPlan(record);
}

async function fetchPlan(record) {
  state.pendingPlanFor = record.id;
  try {
    const data = await api(`/api/analyses/${encodeURIComponent(record.id)}/plan`,
                           { method: "POST" });
    // The user may have opened a different analysis while this was in flight -
    // in that case the slots on screen belong to another record, so drop it.
    if (state.pendingPlanFor !== record.id) return;

    Object.assign(record.result, data.plan || {});
    const planSlot = $("plan-slot");
    const interviewSlot = $("interview-slot");
    if (planSlot) planSlot.innerHTML = planCard(record.result);
    if (interviewSlot) interviewSlot.innerHTML = interviewCard(record.result);
  } catch (err) {
    if (state.pendingPlanFor !== record.id) return;
    const planSlot = $("plan-slot");
    const interviewSlot = $("interview-slot");
    if (interviewSlot) interviewSlot.innerHTML = "";
    if (planSlot) {
      planSlot.innerHTML = `
        <div class="alert alert-warn">
          <div>
            <strong>The 7-day plan could not be generated</strong>
            ${esc(err.message)} Your assessment above is saved.
            <div style="margin-top:.7rem">
              <button class="btn btn-sm" id="retry-plan">Retry the plan</button>
            </div>
          </div>
        </div>`;
      $("retry-plan").onclick = () => {
        planSlot.innerHTML = pendingCard("Your 7-day plan", "Retrying…");
        if (interviewSlot) {
          interviewSlot.innerHTML = pendingCard("Interview preparation", "Retrying…");
        }
        fetchPlan(record);
      };
    }
  } finally {
    if (state.pendingPlanFor === record.id) state.pendingPlanFor = null;
  }
}

function scoreCard(record, r) {
  const score = r.fit_score ?? 0;
  const verified = r.evidence_verified_count ?? 0;
  const total = r.evidence_total_count ?? 0;
  return `
  <div class="card card-lg">
    <div class="card-head">
      <div>
        <h2>${esc(record.role || "Role")} · ${esc(record.company || "Company")}</h2>
        <div class="sub">Analysed ${esc(formatDate(record.createdAt) || "just now")}${
          record.location ? " · " + esc(record.location) : ""}</div>
      </div>
    </div>

    <div class="score-block">
      <div class="ring" data-value="${score}" style="--ring-color:${scoreVar(score)}"
           role="img" aria-label="Application readiness score ${score} out of 100">
        <div class="ring-label">
          <div class="ring-value">${score}</div>
          <div class="ring-unit">Readiness</div>
        </div>
      </div>
      <div style="flex:1;min-width:min(100%,280px)">
        <p style="font-size:1.05rem;font-weight:600;margin-bottom:.5rem">${esc(r.verdict || "")}</p>
        <p class="muted" style="font-size:.9rem">${esc(r.score_rationale || "")}</p>
        <div class="row" style="margin-top:.85rem">
          <span class="badge badge-brand">✓ ${verified}/${total} quotes verified against your resume</span>
          <span class="badge">Preparation score — not a hiring prediction</span>
        </div>
      </div>
    </div>
  </div>`;
}

function readinessCard(r) {
  const readiness = r.readiness || {};
  const dims = [
    ["skill_fit", "Skill fit", "Do your real skills cover what the role asks for?"],
    ["evidence_strength", "Evidence strength", "How concrete and quantified is the proof on the page?"],
    ["keyword_alignment", "Keyword alignment", "Do you use the employer's own vocabulary?"],
    ["application_completeness", "Application completeness", "Does the resume address everything the posting asks for?"],
  ];
  const meters = dims.map(([key, label, help]) => {
    const value = (readiness[key] || {}).score ?? 0;
    const note = (readiness[key] || {}).note || help;
    return `
      <div class="meter">
        <div class="meter-top">
          <span class="meter-name">${esc(label)}</span>
          <span class="meter-val">${value}</span>
        </div>
        <div class="meter-track"><div class="meter-fill ${scoreTone(value)}" data-value="${value}"></div></div>
        <div class="meter-note">${esc(note)}</div>
      </div>`;
  }).join("");

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Application readiness</h3>
        <div class="sub">Four things you can actually change before you hit submit.</div>
      </div>
    </div>
    <div class="grid-2" style="gap:1.3rem">${meters}</div>
  </div>`;
}

function evidenceCard(r) {
  const matches = r.matched_skills || [];
  if (!matches.length) return "";
  const items = matches.map((m) => {
    const meta = STRENGTH_META[m.strength] || STRENGTH_META.moderate;
    return `
    <div class="proof">
      <div class="proof-top">
        <span class="proof-skill">${esc(m.skill)}</span>
        <span class="badge ${meta.cls}">${meta.label}</span>
        ${m.verified
          ? `<span class="badge badge-good" title="This quote was found in your resume text">✓ Verified quote</span>`
          : `<span class="badge badge-warn" title="This quote could not be matched exactly in your resume">⚠ Not found verbatim</span>`}
      </div>
      <div class="proof-req"><strong>Requirement:</strong> ${esc(m.jd_requirement)}</div>
      <div class="quote ${m.verified ? "" : "unverified"}">
        <span class="quote-label">Evidence from your resume</span>${esc(m.resume_quote)}
      </div>
      <div class="faint">${esc(m.explanation)}</div>
    </div>`;
  }).join("");

  const unverified = matches.filter((m) => !m.verified).length;
  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Proof-backed matches</h3>
        <div class="sub">Each match must quote your actual resume. We check every quote against your text.</div>
      </div>
    </div>
    ${unverified ? `<div class="alert alert-warn" style="margin-bottom:1rem">
      <div><strong>${unverified} quote${unverified > 1 ? "s" : ""} could not be matched word-for-word</strong>
      Treat those as paraphrases and confirm them yourself before relying on them.</div></div>` : ""}
    <div class="stack" style="gap:.8rem">${items}</div>
  </div>`;
}

function coverageCard(r) {
  const coverage = r.jd_coverage;
  if (!coverage || !Array.isArray(coverage.segments) || !coverage.segments.length) return "";
  if (!coverage.located_count) return "";

  const body = coverage.segments.map((segment) => {
    const text = esc(segment.text);
    if (segment.kind === "matched") return `<mark class="cov-matched">${text}</mark>`;
    if (segment.kind === "gap") return `<mark class="cov-gap">${text}</mark>`;
    return text;
  }).join("");

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Coverage on the posting itself</h3>
        <div class="sub">The requirements we located in this job description, marked against what your resume proves.</div>
      </div>
      <span class="badge badge-brand spacer-end">${coverage.covered_pct}% of located requirements evidenced</span>
    </div>

    <div class="row" style="gap:1rem;margin-bottom:.9rem">
      <span class="cov-key"><span class="cov-swatch cov-matched"></span>
        Evidenced in your resume (${coverage.matched_count})</span>
      <span class="cov-key"><span class="cov-swatch cov-gap"></span>
        Gap (${coverage.gap_count})</span>
    </div>

    <div class="jd-view">${body}</div>
    <p class="faint" style="margin-top:.8rem">
      Unmarked text is either not a requirement, or was paraphrased rather than quoted so we did not guess at where it belongs.
    </p>
  </div>`;
}

function gapsCard(r) {
  const gaps = r.gaps || [];
  if (!gaps.length) return "";
  const items = gaps.map((g) => {
    const meta = SEVERITY_META[g.severity] || SEVERITY_META.important;
    return `
    <div class="proof">
      <div class="proof-top">
        <span class="proof-skill">${esc(g.skill)}</span>
        <span class="badge ${meta.cls}">${meta.label}</span>
      </div>
      <div class="proof-req"><strong>Asked for:</strong> ${esc(g.jd_requirement)}</div>
      <div class="faint">${esc(g.why_it_matters)}</div>
      <div class="quote" style="border-left-color:var(--brand)">
        <span class="quote-label">Fastest credible proof</span>${esc(g.fastest_credible_proof)}
      </div>
    </div>`;
  }).join("");

  const critical = gaps.filter((g) => g.severity === "critical").length;
  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Gaps, ranked by severity</h3>
        <div class="sub">${critical
          ? `${critical} critical gap${critical > 1 ? "s" : ""} to close first — the rest can wait.`
          : "No critical gaps found. Focus on strengthening evidence."}</div>
      </div>
    </div>
    <div class="stack" style="gap:.8rem">${items}</div>
  </div>`;
}

function planCard(r) {
  const days = r.seven_day_plan || [];
  if (!days.length) return "";
  const totalMinutes = days.reduce((sum, d) => sum + (d.estimated_minutes || 0), 0);
  const items = days.map((d) => `
    <div class="day">
      <div class="day-num">DAY<br>${d.day}</div>
      <div class="day-body">
        <div class="row" style="gap:.5rem">
          <strong style="font-size:.95rem">${esc(d.focus)}</strong>
          <span class="badge">${d.estimated_minutes} min</span>
        </div>
        <ul class="tight">${(d.tasks || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
        <div class="quote" style="border-left-color:var(--brand)">
          <span class="quote-label">Deliverable</span>${esc(d.deliverable)}
        </div>
      </div>
    </div>`).join("");

  const wins = (r.quick_wins || []).map((w) => `<li>${esc(w)}</li>`).join("");
  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Your 7-day plan</h3>
        <div class="sub">${esc(r.plan_summary || "")} · about ${Math.round(totalMinutes / 60)} hours total</div>
      </div>
    </div>
    ${wins ? `<div class="alert alert-info" style="margin-bottom:1.2rem">
      <div><strong>Quick wins — under 15 minutes each</strong>
      <ul class="tight" style="margin-top:.4rem">${wins}</ul></div></div>` : ""}
    <div>${items}</div>
  </div>`;
}

function rewritesCard(r) {
  const rewrites = r.bullet_rewrites || [];
  if (!rewrites.length) return "";
  const items = rewrites.map((b) => `
    <div class="proof">
      <div class="proof-top">
        <span class="badge badge-brand">Supports: ${esc(b.jd_requirement)}</span>
        ${b.verified ? "" : `<span class="badge badge-warn">⚠ Original not found verbatim</span>`}
      </div>
      <div class="diff">
        <div class="diff-side diff-before">
          <span class="quote-label">Before</span>${esc(b.before)}
        </div>
        <div class="diff-side diff-after">
          <span class="quote-label">After</span>${esc(b.after)}
        </div>
      </div>
      <div class="faint">${esc(b.rationale)}</div>
    </div>`).join("");

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Bullet rewrites</h3>
        <div class="sub">Same facts, clearer signal. Rewrites use only what is already in your resume — square brackets mark a real number only you can fill in.</div>
      </div>
    </div>
    <div class="stack" style="gap:.9rem">${items}</div>
  </div>`;
}

function truthGuardCard(r) {
  const flags = r.truth_guard || [];
  if (!flags.length) return "";
  const items = flags.map((f) => `
    <div class="proof" style="border-color:color-mix(in srgb, var(--bad) 30%, transparent)">
      <div class="proof-top">
        <span class="badge badge-bad">Do not claim this</span>
        <span class="proof-skill">${esc(f.tempting_claim)}</span>
      </div>
      <div class="faint">${esc(f.why_unsupported)}</div>
      <div class="quote" style="border-left-color:var(--good)">
        <span class="quote-label">Say this instead</span>${esc(f.honest_alternative)}
      </div>
    </div>`).join("");

  return `
  <div class="card" style="border-color:color-mix(in srgb, var(--bad) 25%, transparent)">
    <div class="card-head">
      <div>
        <h3>🛡️ Truth guard</h3>
        <div class="sub">This job description will tempt you to overstate these. Here is the honest version of each.</div>
      </div>
    </div>
    <div class="stack" style="gap:.8rem">${items}</div>
  </div>`;
}

function interviewCard(r) {
  const questions = r.interview_questions || [];
  if (!questions.length) return "";
  const items = questions.map((q, i) => `
    <div class="proof">
      <div class="proof-top">
        <span class="day-num" style="width:28px;height:28px;border-radius:8px;font-size:.78rem">${i + 1}</span>
        <span class="proof-skill" style="flex:1;min-width:200px">${esc(q.question)}</span>
      </div>
      <div class="proof-req"><strong>Why they will ask:</strong> ${esc(q.why_asked)}</div>
      <ul class="tight">${(q.talking_points || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <div class="quote">
        <span class="quote-label">Grounded in your experience</span>${esc(q.resume_anchor)}
      </div>
    </div>`).join("");

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Interview preparation</h3>
        <div class="sub">Talking points are built strictly from your own resume — nothing here asks you to claim work you have not done.</div>
      </div>
    </div>
    <div class="stack" style="gap:.8rem">${items}</div>
  </div>`;
}

function keywordsCard(r) {
  const keywords = r.missing_keywords || [];
  if (!keywords.length && !r.positioning_summary) return "";
  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Language alignment</h3>
        <div class="sub">Terms this posting uses that never appear in your resume. Use only the ones that truthfully describe your work.</div>
      </div>
    </div>
    ${keywords.length ? `<div class="row" style="gap:.4rem">${
      keywords.map((k) => `<span class="keyword">${esc(k)}</span>`).join("")}</div>` : ""}
    ${r.positioning_summary ? `
      <div class="quote" style="margin-top:1.1rem;border-left-color:var(--brand)">
        <span class="quote-label">Honest positioning paragraph</span>${esc(r.positioning_summary)}
      </div>` : ""}
  </div>`;
}

function manageCard(record) {
  if (!record.id) {
    return `<div class="alert alert-warn"><div><strong>Not saved</strong>
      This analysis could not be written to your history, so it will disappear when you leave the page.</div></div>`;
  }
  const options = state.statuses.map((s) =>
    `<option value="${esc(s)}"${(record.status || "saved") === s ? " selected" : ""}>${esc(STATUS_LABELS[s] || s)}</option>`
  ).join("");

  return `
  <div class="card">
    <div class="card-head">
      <div>
        <h3>Track this application</h3>
        <div class="sub">Saved to your private history. Only you can read it.</div>
      </div>
    </div>
    <div class="grid-2">
      <div class="field">
        <label for="track-status">Status</label>
        <select id="track-status">${options}</select>
      </div>
      <div class="field">
        <label for="track-deadline">Deadline</label>
        <input type="date" id="track-deadline" value="${esc(record.deadline || "")}" />
      </div>
    </div>
    <div class="field" style="margin-top:1rem">
      <label for="track-notes">Notes</label>
      <textarea id="track-notes" style="min-height:90px"
        placeholder="Referral contact, portal login, follow-up date…">${esc(record.notes || "")}</textarea>
    </div>
    <div class="row" style="justify-content:space-between;margin-top:1rem">
      <button id="delete-current" class="btn btn-danger">Delete this analysis</button>
      <div class="row">
        <button id="print-result" class="btn">Print / save as PDF</button>
        <button id="save-tracking" class="btn btn-primary">Save changes</button>
      </div>
    </div>
  </div>`;
}

function wireResultActions(record) {
  const printBtn = $("print-result");
  if (printBtn) printBtn.onclick = () => window.print();

  const saveBtn = $("save-tracking");
  if (saveBtn) {
    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        await api(`/api/analyses/${encodeURIComponent(record.id)}`, {
          method: "PATCH",
          body: {
            status: $("track-status").value,
            notes: $("track-notes").value,
            deadline: $("track-deadline").value,
          },
        });
        record.status = $("track-status").value;
        record.notes = $("track-notes").value;
        record.deadline = $("track-deadline").value;
        toast("Application updated.", "success");
        loadHistory();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        saveBtn.disabled = false;
      }
    };
  }

  const deleteBtn = $("delete-current");
  if (deleteBtn) {
    deleteBtn.onclick = async () => {
      const ok = await confirmDialog({
        title: "Delete this analysis?",
        body: "This permanently removes the resume text, job description and results. It cannot be undone.",
        confirmLabel: "Delete permanently",
        danger: true,
      });
      if (!ok) return;
      try {
        await api(`/api/analyses/${encodeURIComponent(record.id)}`, { method: "DELETE" });
        toast("Analysis deleted.", "success");
        state.current = null;
        hide($("result-body"));
        show($("result-empty"));
        loadHistory();
        setTab("history");
      } catch (err) {
        toast(err.message, "error");
      }
    };
  }
}

/* ==========================================================================
   Analyze flow
   ========================================================================== */

// Stage one only - the plan and interview questions stream in afterwards, so
// these steps describe the assessment and nothing more.
const LOADING_STEPS = [
  "Reading your resume and the job description",
  "Matching each requirement to real evidence in your resume",
  "Verifying every quote against your original text",
  "Scoring readiness and ranking the gaps",
];

function runLoadingAnimation() {
  const host = $("loading-steps");
  host.innerHTML = LOADING_STEPS.map((s, i) =>
    `<div class="loading-step ${i === 0 ? "active" : ""}" data-i="${i}">
       <span class="pulse-dot"></span>${esc(s)}
     </div>`).join("");

  let index = 0;
  const timer = setInterval(() => {
    const steps = host.querySelectorAll(".loading-step");
    if (index < steps.length - 1) {
      steps[index].classList.replace("active", "done");
      steps[index].querySelector(".pulse-dot").style.animation = "none";
      steps[++index].classList.add("active");
    }
  }, 3200);
  return () => clearInterval(timer);
}

function wireCounter(textareaId, counterId, maxKey) {
  const area = $(textareaId);
  const counter = $(counterId);
  const paint = () => {
    const max = state.limits[maxKey] || 20000;
    const length = area.value.length;
    counter.textContent = `${length.toLocaleString()} / ${max.toLocaleString()} characters`;
    counter.classList.toggle("over", length > max);
  };
  area.addEventListener("input", paint);
  paint();
}

function loadSample() {
  $("resume-text").value = SAMPLE.resume;
  $("jd-text").value = SAMPLE.jd;
  $("company").value = SAMPLE.company;
  $("role").value = SAMPLE.role;
  $("resume-text").dispatchEvent(new Event("input"));
  $("jd-text").dispatchEvent(new Event("input"));
  $("dropzone-text").textContent = DROPZONE_LABEL;
  hide($("analyze-error"));
  toast("Sample application loaded. Press Analyse to run it.", "success");
  $("analyze-btn").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function handleAnalyze(event) {
  event.preventDefault();
  const errorBox = $("analyze-error");
  hide(errorBox);

  const payload = {
    resume_text: $("resume-text").value,
    jd_text: $("jd-text").value,
    company: $("company").value,
    role: $("role").value,
  };

  const minResume = state.limits.minResumeChars || 120;
  const minJd = state.limits.minJdChars || 120;
  if (payload.resume_text.trim().length < minResume) {
    errorBox.innerHTML = `<div>Add your resume text first — at least ${minResume} characters so there is something to analyse.</div>`;
    show(errorBox); $("resume-text").focus(); return;
  }
  if (payload.jd_text.trim().length < minJd) {
    errorBox.innerHTML = `<div>Paste the job description — at least ${minJd} characters.</div>`;
    show(errorBox); $("jd-text").focus(); return;
  }

  const button = $("analyze-btn");
  button.disabled = true;
  button.innerHTML = `<span class="spin"></span> Analysing…`;
  hide($("analyze-form"));
  show($("analyze-loading"));
  const stopAnimation = runLoadingAnimation();

  try {
    const data = await api("/api/analyze", { method: "POST", body: payload });
    if (!data.saved && data.saveError) toast(data.saveError, "error");
    renderResult({
      id: data.id, company: data.company, role: data.role, location: data.location,
      status: data.status || "saved", notes: "", deadline: "",
      createdAt: data.createdAt, result: data.result,
    });
    setTab("result");
    loadHistory();
    toast("Assessment ready. Your plan is still generating.", "success");
  } catch (err) {
    errorBox.innerHTML = `<div><strong>Analysis failed</strong>${esc(err.message)}</div>`;
    show(errorBox);
  } finally {
    stopAnimation();
    hide($("analyze-loading"));
    show($("analyze-form"));
    button.disabled = false;
    button.textContent = "Analyse application";
  }
}

/* ---------- PDF upload ---------- */
async function uploadResume(file) {
  if (!file) return;
  const label = $("dropzone-text");
  const original = label.textContent;
  label.textContent = "⏳ Extracting text…";

  const form = new FormData();
  form.append("file", file);
  try {
    const data = await api("/api/extract-resume", { method: "POST", formData: form });
    $("resume-text").value = data.text;
    $("resume-text").dispatchEvent(new Event("input"));
    label.textContent = `✓ ${file.name} — ${data.charCount.toLocaleString()} characters extracted`;
    toast("Resume text extracted. Review it before analysing.", "success");
  } catch (err) {
    label.textContent = original;
    toast(err.message, "error");
  }
}

function wireUpload() {
  const zone = $("dropzone");
  const input = $("resume-file");

  zone.onclick = () => input.click();
  zone.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onchange = () => { uploadResume(input.files[0]); input.value = ""; };

  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => uploadResume(e.dataTransfer.files[0]));
}

/* ==========================================================================
   History + tracker
   ========================================================================== */

async function loadHistory() {
  const host = $("history-body");
  if (!host.dataset.loaded) {
    host.innerHTML = `<div class="card"><div class="skeleton" style="height:52px"></div></div>`.repeat(3);
  }
  try {
    const data = await api("/api/analyses");
    state.history = data.items || [];
    host.dataset.loaded = "1";
    $("history-count").textContent = state.history.length;
    renderHistory();
    renderComparePicker();
  } catch (err) {
    host.innerHTML = `<div class="alert alert-error"><div><strong>Could not load your history</strong>${esc(err.message)}</div></div>`;
  }
}

function renderHistory() {
  const host = $("history-body");
  if (!state.history.length) {
    host.innerHTML = `
      <div class="card empty">
        <div class="empty-icon" aria-hidden="true">🗂️</div>
        <h3>No applications yet</h3>
        <p>Run your first analysis and it will appear here with its score, status and full results.</p>
        <button class="btn btn-primary" style="margin-top:1rem" id="empty-start">Start an analysis</button>
      </div>`;
    $("empty-start").onclick = () => setTab("analyze");
    return;
  }

  host.innerHTML = state.history.map((item) => {
    const options = state.statuses.map((s) =>
      `<option value="${esc(s)}"${item.status === s ? " selected" : ""}>${esc(STATUS_LABELS[s] || s)}</option>`
    ).join("");
    return `
    <div class="hist-item" data-id="${esc(item.id)}">
      <div class="hist-score" style="color:${scoreVar(item.fitScore)}">${item.fitScore}</div>
      <div class="hist-meta">
        <div class="hist-title">${esc(item.role)} · ${esc(item.company)}</div>
        <div class="hist-sub">
          <span>${esc(formatDate(item.createdAt))}</span>
          ${item.criticalGaps ? `<span class="badge badge-bad">${item.criticalGaps} critical gap${item.criticalGaps > 1 ? "s" : ""}</span>` : ""}
          <span class="badge badge-good">✓ ${item.evidenceVerified}/${item.evidenceTotal} verified</span>
          ${item.deadline ? `<span class="badge badge-warn">Due ${esc(item.deadline)}</span>` : ""}
        </div>
      </div>
      <div class="hist-actions">
        <select class="status-select" data-status-for="${esc(item.id)}"
                aria-label="Status for ${esc(item.role)}">${options}</select>
        <button class="btn btn-sm" data-open="${esc(item.id)}">Open</button>
        <button class="btn btn-sm btn-danger" data-delete="${esc(item.id)}" aria-label="Delete">✕</button>
      </div>
    </div>`;
  }).join("");

  host.querySelectorAll("[data-open]").forEach((btn) => {
    btn.onclick = (e) => { e.stopPropagation(); openAnalysis(btn.dataset.open); };
  });
  host.querySelectorAll(".hist-item").forEach((row) => {
    row.onclick = () => openAnalysis(row.dataset.id);
  });
  host.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const ok = await confirmDialog({
        title: "Delete this analysis?",
        body: "This permanently removes the resume text, job description and results. It cannot be undone.",
        confirmLabel: "Delete permanently", danger: true,
      });
      if (!ok) return;
      try {
        await api(`/api/analyses/${encodeURIComponent(btn.dataset.delete)}`, { method: "DELETE" });
        toast("Analysis deleted.", "success");
        if (state.current && state.current.id === btn.dataset.delete) {
          state.current = null; hide($("result-body")); show($("result-empty"));
        }
        loadHistory();
      } catch (err) { toast(err.message, "error"); }
    };
  });
  host.querySelectorAll("[data-status-for]").forEach((select) => {
    select.onclick = (e) => e.stopPropagation();
    select.onchange = async () => {
      try {
        await api(`/api/analyses/${encodeURIComponent(select.dataset.statusFor)}`, {
          method: "PATCH", body: { status: select.value },
        });
        toast(`Marked as ${STATUS_LABELS[select.value] || select.value}.`, "success");
        const row = state.history.find((h) => h.id === select.dataset.statusFor);
        if (row) row.status = select.value;
      } catch (err) { toast(err.message, "error"); }
    };
  });
}

async function openAnalysis(id) {
  // Cancel any in-flight stage-two write aimed at the outgoing record.
  state.pendingPlanFor = null;
  setTab("result");
  hide($("result-empty"));
  show($("result-body"));
  $("result-body").innerHTML = `<div class="card"><div class="loading-panel">
    <div class="pulse-dot"></div><p class="muted">Loading analysis…</p></div></div>`;
  try {
    const record = await api(`/api/analyses/${encodeURIComponent(id)}`);
    renderResult(record);
  } catch (err) {
    $("result-body").innerHTML = `<div class="alert alert-error"><div><strong>Could not open that analysis</strong>${esc(err.message)}</div></div>`;
  }
}

/* ==========================================================================
   Compare roles
   ========================================================================== */

function renderComparePicker() {
  const host = $("compare-picker");
  const max = state.limits.maxCompareItems || 3;

  if (state.history.length < 2) {
    host.innerHTML = `
      <div class="card empty">
        <div class="empty-icon" aria-hidden="true">⚖️</div>
        <h3>Compare needs at least two analyses</h3>
        <p>Analyse a second role and you can see which one your week is best spent on.</p>
      </div>`;
    $("compare-btn").disabled = true;
    return;
  }

  host.innerHTML = state.history.map((item) => `
    <div class="hist-item ${state.compareSelection.has(item.id) ? "selected" : ""}" data-pick="${esc(item.id)}">
      <div class="hist-score" style="color:${scoreVar(item.fitScore)}">${item.fitScore}</div>
      <div class="hist-meta">
        <div class="hist-title">${esc(item.role)} · ${esc(item.company)}</div>
        <div class="hist-sub"><span>${esc(formatDate(item.createdAt))}</span>
          ${item.criticalGaps ? `<span class="badge badge-bad">${item.criticalGaps} critical</span>` : ""}
        </div>
      </div>
      <div class="hist-actions">
        <span class="badge ${state.compareSelection.has(item.id) ? "badge-brand" : ""}">
          ${state.compareSelection.has(item.id) ? "✓ Selected" : "Select"}
        </span>
      </div>
    </div>`).join("");

  host.querySelectorAll("[data-pick]").forEach((row) => {
    row.onclick = () => {
      const id = row.dataset.pick;
      if (state.compareSelection.has(id)) state.compareSelection.delete(id);
      else if (state.compareSelection.size >= max) toast(`Select at most ${max} roles.`, "error");
      else state.compareSelection.add(id);
      renderComparePicker();
    };
  });

  $("compare-btn").disabled = state.compareSelection.size < 2;
}

async function handleCompare() {
  const button = $("compare-btn");
  const host = $("compare-result");
  button.disabled = true;
  button.innerHTML = `<span class="spin"></span> Comparing…`;
  host.innerHTML = `<div class="card"><div class="loading-panel">
    <div class="pulse-dot"></div><p class="muted">Weighing fit against how closeable each gap is…</p></div></div>`;

  try {
    const data = await api("/api/compare", { method: "POST", body: { ids: [...state.compareSelection] } });
    const c = data.comparison || {};
    host.innerHTML = `
      <div class="card card-lg">
        <div class="card-head">
          <div>
            <h3>Strongest fit: ${esc(c.best_fit_role || "")}</h3>
            <div class="sub">Based on current fit and how realistically each gap can be closed.</div>
          </div>
        </div>
        <p class="muted">${esc(c.best_fit_reason || "")}</p>

        <div class="grid-2" style="margin-top:1.4rem;gap:1.4rem">
          <div>
            <h4 style="margin-bottom:.6rem">Recommended order</h4>
            <ol class="tight" style="padding-left:1.3rem">${
              (c.recommended_order || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ol>
          </div>
          <div>
            <h4 style="margin-bottom:.6rem">Gaps shared across roles</h4>
            <div class="row" style="gap:.4rem">${
              (c.shared_gaps || []).map((g) => `<span class="keyword">${esc(g)}</span>`).join("")
              || `<span class="faint">No overlapping gaps found.</span>`}</div>
            <p class="faint" style="margin-top:.6rem">Closing these pays off for more than one application.</p>
          </div>
        </div>

        <h4 style="margin:1.4rem 0 .6rem">Honest tradeoffs</h4>
        <ul class="tight">${(c.tradeoffs || []).map((t) => `<li>${esc(t)}</li>`).join("")}</ul>

        <div class="grid-2" style="margin-top:1.5rem;gap:1rem">${
          (data.roles || []).map((r) => `
            <div class="proof">
              <div class="proof-top">
                <span class="proof-skill">${esc(r.role)} · ${esc(r.company)}</span>
                <span class="badge" style="color:${scoreVar(r.fitScore)}">${r.fitScore}/100</span>
              </div>
              ${["skill_fit", "evidence_strength", "keyword_alignment", "application_completeness"]
                .map((k) => {
                  const value = (r.readiness?.[k] || {}).score ?? 0;
                  const label = { skill_fit: "Skill fit", evidence_strength: "Evidence",
                                  keyword_alignment: "Keywords", application_completeness: "Completeness" }[k];
                  return `<div class="meter">
                    <div class="meter-top"><span class="meter-name">${label}</span><span class="meter-val">${value}</span></div>
                    <div class="meter-track"><div class="meter-fill ${scoreTone(value)}" style="width:${value}%"></div></div>
                  </div>`;
                }).join("")}
            </div>`).join("")}
        </div>
      </div>`;
  } catch (err) {
    host.innerHTML = `<div class="alert alert-error"><div><strong>Comparison failed</strong>${esc(err.message)}</div></div>`;
  } finally {
    button.disabled = state.compareSelection.size < 2;
    button.textContent = "Compare selected";
  }
}

/* ==========================================================================
   Auth + boot
   ========================================================================== */

function onSignedIn(user) {
  state.user = user;
  $("user-email").textContent = user.email || "Signed in";
  $("user-avatar").textContent = (user.displayName || user.email || "?").trim()[0].toUpperCase();
  hide($("signin-btn")); show($("user-chip")); show($("signout-btn"));
  setView("app");
  setTab("analyze");
  loadHistory();
}

function onSignedOut() {
  state.user = null;
  state.history = [];
  state.current = null;
  state.pendingPlanFor = null;
  state.compareSelection.clear();
  $("history-body").dataset.loaded = "";
  show($("signin-btn")); hide($("user-chip")); hide($("signout-btn"));
  setView("landing");
}

async function boot() {
  initTheme();

  let settings;
  try {
    const response = await fetch("/api/config");
    settings = await response.json();
    if (!response.ok) throw new Error(settings.error || "Configuration unavailable");
  } catch (err) {
    $("config-error-detail").textContent = err.message;
    setView("config-error");
    return;
  }

  state.limits = settings.limits || {};
  state.statuses = settings.statuses || ["saved"];

  const app = initializeApp(settings.firebase);
  state.auth = getAuth(app);
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });

  const signIn = async () => {
    try {
      await signInWithPopup(state.auth, provider);
    } catch (err) {
      if (err.code === "auth/popup-closed-by-user" || err.code === "auth/cancelled-popup-request") return;
      if (err.code === "auth/unauthorized-domain") {
        toast("This domain is not authorised in Firebase Auth. Add it under Authentication → Settings → Authorized domains.", "error");
        return;
      }
      toast(`Sign-in failed: ${err.message}`, "error");
    }
  };

  $("signin-btn").onclick = signIn;
  $("signin-hero").onclick = signIn;
  $("signout-btn").onclick = () => signOut(state.auth);

  document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => setTab(tab.dataset.tab); });
  $("analyze-form").onsubmit = handleAnalyze;
  $("load-sample").onclick = loadSample;
  $("refresh-history").onclick = loadHistory;
  $("compare-btn").onclick = handleCompare;
  wireUpload();
  wireCounter("resume-text", "resume-counter", "maxResumeChars");
  wireCounter("jd-text", "jd-counter", "maxJdChars");

  $("delete-all-btn").onclick = async () => {
    if (!state.history.length) { toast("There is nothing to delete."); return; }
    const ok = await confirmDialog({
      title: `Delete all ${state.history.length} analyses?`,
      body: "Every stored resume, job description and result is permanently removed. This cannot be undone.",
      confirmLabel: "Delete everything", danger: true,
    });
    if (!ok) return;
    try {
      await Promise.all(state.history.map((item) =>
        api(`/api/analyses/${encodeURIComponent(item.id)}`, { method: "DELETE" })));
      state.current = null;
      hide($("result-body")); show($("result-empty"));
      toast("All analyses deleted.", "success");
      loadHistory();
    } catch (err) { toast(err.message, "error"); }
  };

  onAuthStateChanged(state.auth, (user) => (user ? onSignedIn(user) : onSignedOut()));
}

boot();
