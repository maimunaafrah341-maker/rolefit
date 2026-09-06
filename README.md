# RoleFit

**Turn job descriptions into an honest, actionable application plan.**

RoleFit is an AI application copilot for students and early-career applicants. Paste your
resume and a job description, and it returns a proof-backed readiness assessment: every
matched skill quotes the exact line of your resume that proves it, every gap is ranked by
severity and converted into a realistic 7-day plan, and a **Truth Guard** flags the claims
the posting tempts you to make that your resume does not support.

> RoleFit measures how well-prepared an application is. It does **not** predict hiring
> outcomes, and it will not help anyone fabricate experience.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Local setup](#local-setup)
- [Firebase setup](#firebase-setup)
- [Firestore rules deployment](#firestore-rules-deployment)
- [Environment variables](#environment-variables)
- [Cloud Run deployment](#cloud-run-deployment)
- [API reference](#api-reference)
- [Manual test checklist](#manual-test-checklist)
- [Security model](#security-model)
- [Demo script](#demo-script)
- [Troubleshooting](#troubleshooting)

---

## What it does

| Feature | What makes it different |
|---|---|
| **Proof-backed fit** | Every matched skill cites a verbatim resume quote. The server re-checks each quote against your actual text and labels any it cannot find, so hallucinated evidence is visible rather than hidden. |
| **JD coverage map** | Highlights the job description itself, marking every requirement green where your resume proves it and amber where it does not. Requirements the model paraphrased rather than quoted are left unmarked rather than guessed at. |
| **Truth guard** | Names the specific overstatements this posting invites, explains why the resume does not support them, and offers an honest alternative. |
| **Gap-to-action plan** | Critical / important / nice-to-have gaps become a 7-day plan where each day ends in a real deliverable — never "read about X". |
| **ATS rewrite mode** | Before/after bullet rewrites using only facts already in the resume, each mapped to the JD requirement it answers. |
| **Readiness score** | Four independent axes — skill fit, evidence strength, keyword alignment, application completeness — all things the applicant can change. |
| **Interview prep** | Five likely questions with talking points grounded strictly in the candidate's own experience. |
| **Compare roles** | Weighs 2–3 saved applications by current fit *and* how closeable each gap is. |
| **Application tracker** | Saved → Applied → Assessment → Interview → Rejected / Offer, with notes and deadlines. |
| **Privacy by design** | Per-user isolation enforced twice (server-side token verification + Firestore rules), with real one-click deletion. |

---

## Architecture

```
Browser (Firebase JS SDK v10, ES modules)
  │  Google sign-in popup → Firebase ID token
  │  Authorization: Bearer <token>   ← on every API call
  ▼
Flask (gunicorn, 2 workers × 8 threads)
  ├── firebase_auth.py     verify_id_token on every protected route → g.uid
  ├── schemas.py           request validation + AI response contracts + normalisers
  ├── resume_parser.py     PDF → text (magic bytes, size, page and encryption checks)
  ├── gemini_client.py     2 schema-constrained Gemini calls, staged
  └── firestore_client.py  all reads/writes scoped to users/{uid}/analyses
                                │
                                ▼  Admin SDK (bypasses rules by design)
                          Firestore  ← client writes denied by security rules
```

**Files**

| File | Role |
|---|---|
| `app.py` | Routes, error handlers, security headers |
| `config.py` | Every environment variable, declared once |
| `schemas.py` | Pydantic AI contracts, value normalisers, quote verification, request validators |
| `gemini_client.py` | Prompting, staged generation, retry, prompt-injection fencing |
| `firestore_client.py` | Ownership-scoped data access |
| `firebase_auth.py` | ID token verification decorator |
| `resume_parser.py` | PDF upload validation and text extraction |
| `templates/index.html` | App shell |
| `static/css/app.css` | Design system (light + dark tokens) |
| `static/js/app.js` | Auth lifecycle, API client, staged rendering |
| `static/js/sample-data.js` | The sample resume + posting behind "Try a sample" |
| `firestore.rules` | Per-user isolation, client writes denied |

### Two AI calls, staged

A single prompt producing the whole analysis is a reliability trap — the longer a
constrained decode runs, the more likely it truncates. RoleFit splits the work into
*assess the evidence* (`POST /api/analyze`) and *plan the actions*
(`POST /api/analyses/<id>/plan`), each validated against its own Pydantic schema.

They are separate HTTP requests so the browser paints the score, evidence and gaps in about
12 seconds while the plan and interview questions stream into reserved slots afterwards —
instead of holding a spinner for the full 30.

Staging also makes the plan better, not just faster: the second call receives the gaps the
first call actually found and ranked, so the week is built around named weaknesses rather
than re-derived from the raw documents, and cannot contradict the assessment on screen.

### Three layers of output safety

1. `response_schema` constrains generation at the API level.
2. Pydantic re-validates the JSON on arrival; one retry with a stricter nudge on failure.
3. `normalise_core` / `normalise_plan` repair plausible-but-off values (`"must-have"` →
   `critical`), clamp scores to 0–100, and force exactly 7 days and 5 questions.

Then `verify_quotes` checks every claimed resume quote against the real resume text and
labels the ones it cannot find. **That check is the product**: it is what separates
"the AI says you know Kubernetes" from "here is the line where you said so".

---

## Local setup

Requires **Python 3.11+**.

```bash
# 1. From the project directory
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
copy .env.example .env         # Windows   (cp .env.example .env elsewhere)
#    then edit .env and fill in the values

# 4. Run
python app.py
```

Open <http://localhost:8080>.

If the page says *"RoleFit is not configured yet"*, the `FIREBASE_*` variables are missing
from `.env` — see below. `/healthz` returns `{"status":"ok"}` regardless, so you can always
tell the server apart from the configuration.

---

## Firebase setup

**These steps require your Google account — they cannot be automated from here.**

### 1. Create the project
1. Go to <https://console.firebase.google.com> → **Add project**.
2. Name it (e.g. `rolefit`). Google Analytics is optional.

### 2. Enable Google sign-in
1. Left sidebar → **Security → Authentication** → **Get started**.
   (Console layouts change; the **Search for products** box at the top of the sidebar
   is the reliable way to find Authentication if the category has moved.)
2. **Sign-in method → Google → Enable**, choose a support email, **Save**.
3. **Authentication → Settings → Authorized domains** — confirm `localhost` is listed.
   After deploying, **add your Cloud Run domain here too** (`*.run.app`). Sign-in fails
   with `auth/unauthorized-domain` until you do.

### 3. Create the Firestore database
1. Left sidebar → **Databases & Storage → Firestore Database** → **Create database**.
   (Or search for "Firestore" in the sidebar search box.)
2. Choose **production mode** (the rules in this repo replace the defaults).
3. Pick a region and keep it consistent with your Cloud Run region.

### 4. Get the web config
1. **Project settings (⚙) → General → Your apps → Web (`</>`)**.
2. Register the app; copy the `firebaseConfig` values into `.env` as `FIREBASE_API_KEY`,
   `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`,
   `FIREBASE_MESSAGING_SENDER_ID`, `FIREBASE_APP_ID`.

> These web values are **public by design** — the Firebase JS SDK ships them to every
> browser. They are not secrets. Access control comes from Firestore rules and server-side
> token verification, never from hiding them.

### 5. Service account for local development
1. **Project settings → Service accounts → Generate new private key**.
2. Save it as `service-account.json` **in the project root** (already git-ignored).
3. Set `GOOGLE_APPLICATION_CREDENTIALS=./service-account.json` in `.env`.

This is only for local runs. On Cloud Run, the service identity supplies credentials
automatically — **never** set that variable or ship a key file in production.

### 6. Gemini API key
1. <https://aistudio.google.com/apikey> → **Create API key**.
2. Put it in `.env` as `GEMINI_API_KEY`. This one **is** a secret and stays server-side.

---

## Firestore rules deployment

The rules in `firestore.rules` allow a user to read only their own documents and **deny all
client writes**, because every write in this product goes through the backend's Admin SDK
(which bypasses rules by design). Granting client writes would add attack surface for no
benefit.

**Option A — Firebase CLI (recommended)**

```bash
npm install -g firebase-tools
firebase login
firebase init firestore     # select your project; keep firestore.rules when prompted
firebase deploy --only firestore:rules
```

**Option B — Console**
Copy the contents of `firestore.rules` into
**Firestore Database → Rules** and click **Publish**.

Verify in the **Rules Playground**: a `get` on
`/users/some-other-uid/analyses/abc` while authenticated as a different UID must be
**denied**, and any `create` must be denied even for your own UID.

---

## Environment variables

| Variable | Required | Secret | Notes |
|---|---|---|---|
| `GEMINI_API_KEY` | yes | **yes** | Google AI Studio key. Server-side only. |
| `GEMINI_MODEL` | no | no | Default `gemini-2.5-flash`. |
| `FIREBASE_API_KEY` | yes | no | Public web SDK config. |
| `FIREBASE_AUTH_DOMAIN` | yes | no | e.g. `your-project.firebaseapp.com` |
| `FIREBASE_PROJECT_ID` | yes | no | |
| `FIREBASE_STORAGE_BUCKET` | no | no | |
| `FIREBASE_MESSAGING_SENDER_ID` | no | no | |
| `FIREBASE_APP_ID` | no | no | |
| `GOOGLE_APPLICATION_CREDENTIALS` | local only | **path to a secret** | Never set on Cloud Run. |
| `PORT` | no | no | Cloud Run injects this. |
| `FLASK_DEBUG` | no | no | Local only; gunicorn never reads it. |
| `GEMINI_TIMEOUT_S` | no | no | Default 90. |
| `MAX_UPLOAD_BYTES` | no | no | Default 5 MB. |
| `MAX_RESUME_CHARS` / `MAX_JD_CHARS` | no | no | Default 20 000 each. |

---

## Cloud Run deployment

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud services enable run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    firestore.googleapis.com

gcloud run deploy rolefit \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --timeout 300 \
  --set-env-vars "GEMINI_API_KEY=your-key,FIREBASE_API_KEY=...,FIREBASE_AUTH_DOMAIN=your-project.firebaseapp.com,FIREBASE_PROJECT_ID=your-project-id,FIREBASE_APP_ID=...,FIREBASE_MESSAGING_SENDER_ID=...,FIREBASE_STORAGE_BUCKET=..."
```

**After the first deploy**, copy the `https://rolefit-….run.app` URL into
**Firebase → Authentication → Settings → Authorized domains**, or sign-in will fail.

### Better: keep the key in Secret Manager

```bash
echo -n "your-gemini-key" | gcloud secrets create gemini-api-key --data-file=-

gcloud run deploy rolefit --source . --region us-central1 --allow-unauthenticated \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest" \
  --set-env-vars "FIREBASE_API_KEY=...,FIREBASE_PROJECT_ID=...,FIREBASE_AUTH_DOMAIN=..."
```

The runtime service account needs `roles/datastore.user` (Firestore) and
`roles/firebaseauth.viewer` (token verification). The default compute service account
usually has enough; if `/api/analyses` returns 503, grant them explicitly:

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/datastore.user"
```

`.gcloudignore` and `.dockerignore` both exclude `.env` and `service-account.json`, so
credentials are never uploaded to Cloud Build or baked into the image.

---

## API reference

All `[auth]` routes require `Authorization: Bearer <firebase-id-token>` and act only on the
verified caller's own data.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Dashboard |
| `GET` | `/healthz` | Liveness probe, no dependencies |
| `GET` | `/api/config` | Public Firebase web config + input limits |
| `POST` | `/api/extract-resume` | `[auth]` multipart PDF → text |
| `POST` | `/api/analyze` | `[auth]` stage one: resume + JD → assessment, saved |
| `POST` | `/api/analyses/<id>/plan` | `[auth]` stage two: 7-day plan + interview prep. Idempotent — returns the stored plan if one already exists |
| `GET` | `/api/analyses` | `[auth]` history summaries |
| `GET` | `/api/analyses/<id>` | `[auth]` one full analysis |
| `PATCH` | `/api/analyses/<id>` | `[auth]` status / notes / deadline |
| `DELETE` | `/api/analyses/<id>` | `[auth]` permanent delete |
| `POST` | `/api/compare` | `[auth]` compare 2–3 saved analyses |

Errors are always JSON: `{"error": "human readable", "code": "machine_readable"}`.
A `401` with `code: "token_expired"` tells the client to refresh its token and retry once.

---

## Security model

- **Token verification on every protected route.** No route trusts a UID from a request
  body; the UID always comes from the verified token.
- **Bearer tokens, no session cookies** — CSRF is structurally impossible, since a
  cross-site form post cannot set an `Authorization` header.
- **No privileged credentials in the browser.** The client holds a short-lived ID token
  scoped to one user; all Firestore access is server-side under the Admin SDK.
- **Defence in depth.** Even if the frontend were fully compromised, Firestore rules
  independently block cross-user reads and *all* client writes.
- **Field whitelisting.** `PATCH` accepts only `status`, `notes`, `deadline`. Scores,
  evidence, and ownership are not client-writable through any code path.
- **Upload hardening.** Extension + magic-byte + size + page-count + encryption checks;
  size is enforced on bytes actually read, not the client-supplied `Content-Length`.
- **Prompt-injection fencing.** Resumes and job descriptions are pasted from the open web.
  Control characters are stripped, the delimiter sequence is neutralised so pasted text
  cannot close the data block, and the system preamble instructs the model to treat fenced
  content as data and to report instruction-like text as a red flag.
- **Error hygiene.** Raw exceptions are logged server-side, never returned to the client.
- **Security headers** on every response: `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, a restrictive CSP, and `Cache-Control: no-store` on all API responses.

---

## Manual test checklist

- [ ] `/healthz` returns `{"status":"ok"}`; with `FIREBASE_*` unset the page shows the
      config-error card rather than crashing
- [ ] Sign in with Google lands on the dashboard; Sign out returns to the landing page
- [ ] **Try a sample** fills all four fields and the character counters update
- [ ] Analyse → score ring, four readiness meters and proof cards render
- [ ] The score appears while the plan and interview cards still read *Generating*, then
      both fill in without the page jumping
- [ ] **Check a "✓ Verified quote" badge** → find that exact line in the resume
- [ ] Add a fabricated bullet the resume does not contain → it is flagged
      "⚠ Not found verbatim"
- [ ] Coverage card: green and amber spans sit on real requirement text, and the job
      description still reads as unbroken prose
- [ ] Reopen that analysis from History → the plan renders instantly (no second
      Gemini call; the response reports `cached: true`)
- [ ] Upload a PDF → text populates. A `.docx`, a renamed `.exe` and a >5 MB file
      are each rejected with a different message
- [ ] Submit under 120 characters → inline error, no request sent
- [ ] History: change status inline, set a deadline and notes, delete → confirm
      dialog → the row disappears
- [ ] Compare two roles; try selecting a fourth → blocked at three
- [ ] Toggle dark/light and reload → the choice persists. Resize to 375px → no
      horizontal scrolling
- [ ] Sign in as a second Google account → the history is empty (isolation)
- [ ] **Rules Playground:** a `get` on another UID's document is denied, and a `create` on
      your own is also denied

---

## Demo script

**90 seconds.** Have two analyses already saved so History and Compare are populated, and
keep a real job description in your clipboard.

| Time | Say | Do |
|---|---|---|
| **0:00–0:12** | "Every applicant has the same problem: you paste your resume into an AI, it tells you you're a 92% match, and you have no idea whether it read your resume or made it up. RoleFit shows its work." | Landing page. Point at the tagline. |
| **0:12–0:25** | "Real resume, real internship posting." | Sign in with Google, hit **Try a sample** (or paste your own / drop a PDF), then click **Analyse**. |
| **0:25–0:40** | "The assessment comes back first so you can start reading, and the plan streams in behind it. Both are schema-validated JSON." | Let the loading steps play; the score lands while the plan card still reads *Generating*. |
| **0:40–0:55** | "A 68 readiness score, and here's *why*. Four dimensions you can actually change. And every matched skill quotes the exact line of the resume that proves it — we re-check every quote against the original text, so if the model invents one, it gets flagged, not hidden." | Scroll: score ring → readiness meters → **proof-backed matches**. Point at a green ✓ Verified badge. |
| **0:52–0:58** | "And here it is on the posting itself — green is what your resume proves, amber is a gap. No flipping between two documents." | Scroll to **Coverage on the posting itself**. |
| **0:58–1:10** | "**This** is the part I care about. The Truth Guard. This posting asks for Kubernetes. The resume doesn't support it. Most AI tools would help you word around that. Ours tells you not to claim it — and gives you an honest alternative." | Scroll to **Truth Guard**. Pause on one card. |
| **1:10–1:22** | "The gaps become a 7-day plan where every day ends in something real you could actually put on your resume. Plus five interview questions with talking points built only from experience they actually have." | Scroll through the plan and interview cards. |
| **1:22–1:30** | "Everything saves to a private per-user history with a full tracker — and Compare tells you which of three roles your week is best spent on. It helps you prepare. It never claims to predict who gets hired." | Click **History**, then **Compare roles** on two pre-saved analyses. |

**The line to close on:** *"Everyone else is building tools to help you beat the filter.
We built the one that tells you the truth."*

### Judging points

**1. Problem.** Students fire off dozens of applications with no feedback loop. Existing AI
resume tools optimise for a number, so they systematically reward exaggeration — and
applicants can't tell a real match from a hallucinated one. The cost is misdirected effort
and, increasingly, resumes that overstate.

**2. Technical innovation.** *Verified-evidence AI output.* The model must cite a verbatim
resume quote for every claimed match, and the server independently re-checks each quote
against the original text and labels any it can't ground — a hallucination detector on the
UI, not buried in a log. The same grounding machinery, pointed at the posting instead of the
resume, produces the coverage map. Around it: two parallel schema-constrained Gemini calls for
reliability and latency, three layers of output validation (constrained decode → Pydantic →
value normalisation), prompt-injection fencing on untrusted pasted text, and a security
model where the browser never holds a privileged credential.

**3. Real-world impact.** RoleFit converts a rejection-shaped void into a week of concrete
work. The Truth Guard is a deliberate ethical stance in a category racing the other
direction: it makes the honest path the *easier* path by giving applicants better language
for the experience they genuinely have. It runs on Cloud Run at near-zero idle cost, so
scaling to a whole campus is a deploy, not a fundraise.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "RoleFit is not configured yet" | `FIREBASE_API_KEY` / `FIREBASE_PROJECT_ID` missing from `.env`. Restart after editing. |
| `auth/unauthorized-domain` on sign-in | Add the domain under **Firebase → Authentication → Settings → Authorized domains**. |
| "A project ID is required to access the auth service" | `GOOGLE_APPLICATION_CREDENTIALS` is unset or points at a missing file. Locally, download `service-account.json`. |
| Plan card shows an error but the score is fine | Stage two failed on its own; the assessment is already saved. Click **Retry the plan**. |
| Analysis returns 502 | Check `GEMINI_API_KEY`, quota, and that `GEMINI_MODEL` still exists in AI Studio. Server logs carry the real reason. |
| History returns 503 | The runtime service account lacks `roles/datastore.user`, or Firestore was never created. |
| Popup blocked | Sign-in must be triggered by a real click — don't script it. |
| PDF extracts nothing | It's a scan or image export. Paste the text instead; the message says so. |
| Sign-in loop with "token used too early" | Clock skew. A 15-second tolerance is already applied; sync the system clock if it persists. |
