#!/usr/bin/env bash
#
# Post-deploy verification. Exits non-zero on the first failed check so it can
# gate a release.
#
# Usage: ./scripts/smoke_test.sh <PROJECT_ID> [REGION]

set -euo pipefail

PROJECT_ID="${1:?usage: smoke_test.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"

URL="$(gcloud run services describe sentinelai-triage \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
TOKEN="$(gcloud auth print-identity-token)"

pass() { echo "  PASS  $1"; }
fail() {
  echo "  FAIL  $1"
  exit 1
}

check() {
  # check <description> <command...>
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    pass "${description}"
  else
    fail "${description}"
  fi
}

SIGNAL='{"service":"smoke-test","severity":"ERROR","text":"psycopg2.OperationalError: connection pool exhausted on db-primary"}'

triage() {
  curl -sS -f -X POST "${URL}/v1/analyze" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${SIGNAL}"
}

echo "==> Target: ${URL}"

echo "==> Health"
check "liveness" curl -sS -f "${URL}/healthz" -H "Authorization: Bearer ${TOKEN}"
check "readiness (Firestore reachable)" curl -sS -f "${URL}/readyz" -H "Authorization: Bearer ${TOKEN}"

echo "==> Authentication"
# Unauthenticated calls must be rejected by Cloud Run IAM before reaching the app.
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "${URL}/v1/incidents")"
case "${CODE}" in
401 | 403) pass "unauthenticated request rejected (${CODE})" ;;
*) fail "endpoint is publicly reachable (HTTP ${CODE})" ;;
esac

echo "==> Triage"
RESPONSE="$(triage)"
case "${RESPONSE}" in
*'"fingerprint"'*) pass "triage returned a verdict" ;;
*) fail "no verdict: ${RESPONSE}" ;;
esac

FINGERPRINT="$(printf '%s' "${RESPONSE}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"

echo "==> Deduplication"
SECOND="$(triage)"

case "${SECOND}" in
*'"action":"suppressed"'*) pass "repeat signal suppressed (${FINGERPRINT})" ;;
*) fail "duplicate was not suppressed: ${SECOND}" ;;
esac

case "${SECOND}" in
*'"ai_invoked":false'*) pass "no second Gemini call" ;;
*) fail "Gemini was called again for a duplicate" ;;
esac

echo
echo "All checks passed."
