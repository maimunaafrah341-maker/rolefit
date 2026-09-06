#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# RoleFit -> Cloud Run
#
# Reads .env and builds the deploy command, so no secret is ever typed into a
# terminal, pasted into a chat, or left in shell history. The script itself
# contains no credentials and is safe to commit.
#
#   bash deploy.sh
#
# Prerequisites (see README): gcloud installed and authenticated, billing
# enabled on the project (Blaze), and the required APIs turned on - this script
# enables them for you on first run.
# ---------------------------------------------------------------------------
set -euo pipefail

SERVICE="rolefit"
REGION="${REGION:-asia-south1}"   # keep this matching your Firestore location

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example and fill it in first." >&2
  exit 1
fi

# --- Load .env, skipping comments and blanks -------------------------------
declare -A ENVMAP
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"                       # tolerate CRLF
  case "$line" in ''|'#'*) continue ;; esac
  [[ "$line" != *=* ]] && continue
  key="${line%%=*}"; value="${line#*=}"
  key="$(echo "$key" | tr -d '[:space:]')"
  [ -z "$value" ] && continue
  ENVMAP["$key"]="$value"
done < .env

PROJECT_ID="${ENVMAP[FIREBASE_PROJECT_ID]:-}"
if [ -z "$PROJECT_ID" ]; then
  echo "ERROR: FIREBASE_PROJECT_ID is not set in .env" >&2
  exit 1
fi

# --- Variables the container needs -----------------------------------------
# GOOGLE_APPLICATION_CREDENTIALS is deliberately EXCLUDED: on Cloud Run the
# service identity supplies credentials automatically, and pointing at a
# key file that is not in the image would break startup.
# PORT is excluded too - Cloud Run injects it.
WANTED=(
  GEMINI_API_KEY
  GEMINI_MODEL
  GEMINI_FALLBACK_MODELS
  FIREBASE_API_KEY
  FIREBASE_AUTH_DOMAIN
  FIREBASE_PROJECT_ID
  FIREBASE_STORAGE_BUCKET
  FIREBASE_MESSAGING_SENDER_ID
  FIREBASE_APP_ID
)

missing=()
for key in GEMINI_API_KEY FIREBASE_API_KEY FIREBASE_AUTH_DOMAIN FIREBASE_PROJECT_ID; do
  [ -z "${ENVMAP[$key]:-}" ] && missing+=("$key")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: required values missing from .env: ${missing[*]}" >&2
  exit 1
fi

# Use a custom delimiter (^##^) so a value containing a comma - such as
# GEMINI_FALLBACK_MODELS - does not get split into separate variables.
PAIRS=""
SET_KEYS=""
for key in "${WANTED[@]}"; do
  value="${ENVMAP[$key]:-}"
  [ -z "$value" ] && continue
  PAIRS="${PAIRS}##${key}=${value}"
  SET_KEYS="${SET_KEYS} ${key}"
done
PAIRS="^##^${PAIRS#\#\#}"

echo "==========================================================="
echo "  Deploying $SERVICE"
echo "  project : $PROJECT_ID"
echo "  region  : $REGION"
echo "  env vars:$SET_KEYS"
echo "  (values read from .env and never printed)"
echo "==========================================================="
echo

gcloud config set project "$PROJECT_ID" --quiet

echo "--- Enabling required APIs (no-op if already on) ---"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  --quiet

echo
echo "--- Building and deploying (first build takes ~5 minutes) ---"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 3 \
  --min-instances 0 \
  --set-env-vars "$PAIRS" \
  --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
DOMAIN="${URL#https://}"

cat <<EOF

===========================================================
  DEPLOYED:  $URL
===========================================================

ONE STEP LEFT - sign-in will fail until you do this:

  1. Open https://console.firebase.google.com/project/$PROJECT_ID/authentication/settings
  2. Under "Authorized domains", click "Add domain"
  3. Paste exactly:  $DOMAIN
  4. Save, then hard-refresh the site (Ctrl+Shift+R)

Health check (should print ok):
  curl $URL/healthz

Cost guards applied: max-instances 3, min-instances 0 (scales to zero
when idle, so an unused service costs nothing).
EOF
