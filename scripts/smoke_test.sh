#!/usr/bin/env bash
#
# Post-deploy verification. Exits non-zero on the first failed check so it can
# gate a release, and prints the actual HTTP status and response body when
# something fails — a check that only says "FAIL" tells you nothing.
#
# Usage: ./scripts/smoke_test.sh <PROJECT_ID> [REGION]

set -euo pipefail

PROJECT_ID="${1:?usage: smoke_test.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"

# gcloud emits CRLF on Windows, and $(...) strips the trailing newline but not
# the carriage return. An unstripped \r turns a bearer token into a malformed
# header and a URL into an unroutable one, which presents as an auth failure
# with no obvious cause. Strip it from every gcloud capture in this file.
gc() { gcloud "$@" | tr -d '\r'; }

URL="$(gc run services describe sentinelai-triage \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
TOKEN="$(gc auth print-identity-token)"

if [ -z "${URL}" ]; then
  echo "  FAIL  could not resolve the service URL — is sentinelai-triage deployed in ${REGION}?"
  exit 1
fi

pass() { echo "  PASS  $1"; }
fail() {
  echo "  FAIL  $1"
  exit 1
}

# request <METHOD> <PATH> [DATA] -> sets HTTP_STATUS and HTTP_BODY
request() {
  local method="$1" path="$2" data="${3:-}"
  local raw
  local -a args=(-sS -X "${method}" -w $'\n%{http_code}' -H "Authorization: Bearer ${TOKEN}")
  if [ -n "${data}" ]; then
    args+=(-H "Content-Type: application/json" -d "${data}")
  fi

  raw="$(curl "${args[@]}" "${URL}${path}" 2>&1 || true)"
  HTTP_STATUS="${raw##*$'\n'}"
  HTTP_BODY="${raw%$'\n'*}"
}

expect() {
  local description="$1" method="$2" path="$3" want="$4" data="${5:-}"
  request "${method}" "${path}" "${data}"
  if [ "${HTTP_STATUS}" = "${want}" ]; then
    pass "${description} (HTTP ${HTTP_STATUS})"
  else
    echo "  FAIL  ${description}"
    echo "        expected HTTP ${want}, got ${HTTP_STATUS:-<none>}"
    echo "        response: ${HTTP_BODY}"
    if [ "${HTTP_STATUS}" = "403" ]; then
      echo
      echo "        403 means Cloud Run rejected the caller before the app saw it."
      echo "        Grant yourself invoker access in terraform.tfvars:"
      echo "          operator_members = [\"user:\$(gcloud config get-value account)\"]"
    fi
    exit 1
  fi
}

echo "==> Target: ${URL}"

# `terraform apply` deploys var.container_image, which defaults to Google's
# sample "hello" container so the very first apply succeeds before any image
# exists. That container 404s on every path except "/", which looks exactly
# like a broken application. Catch it here rather than letting it waste an hour.
RUNNING_IMAGE="$(gc run services describe sentinelai-triage \
  --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)' 2>/dev/null || true)"

case "${RUNNING_IMAGE}" in
*cloudrun/container/hello*)
  echo
  echo "  FAIL  The service is still running the placeholder image:"
  echo "          ${RUNNING_IMAGE}"
  echo "        That image returns 404 for /healthz and every other path."
  echo "        Build and deploy the real image:"
  echo "          make deploy PROJECT_ID=${PROJECT_ID}"
  exit 1
  ;;
esac
echo "==> Image:  ${RUNNING_IMAGE:-unknown}"

SIGNAL='{"service":"smoke-test","severity":"ERROR","text":"psycopg2.OperationalError: connection pool exhausted on db-primary"}'

echo "==> Health"
# /livez, not /healthz: the deployed service has /healthz intercepted
# upstream of the container. Same handler, reachable path.
expect "liveness" GET /livez 200
expect "readiness (Firestore reachable)" GET /readyz 200

echo "==> Authentication"
# Unauthenticated calls must be rejected by Cloud Run IAM before reaching the
# app. The GFE may answer 401, 403 or 404 depending on how the credential is
# malformed — 404 is deliberate, so a private service cannot be enumerated by
# probing paths. All three prove the same thing: the request died upstream of
# the container. Only a 2xx here would be a real finding.
CODE="$(curl -sS -o /dev/null -w '%{http_code}' "${URL}/v1/incidents" || true)"
case "${CODE}" in
401 | 403 | 404) pass "unauthenticated request rejected at the GFE (HTTP ${CODE})" ;;
2*) fail "endpoint is publicly reachable (HTTP ${CODE}) — check the invoker IAM policy" ;;
*) fail "unexpected response to an unauthenticated request (HTTP ${CODE})" ;;
esac

echo "==> Triage"
expect "triage returned a verdict" POST /v1/analyze 200 "${SIGNAL}"
FINGERPRINT="$(printf '%s' "${HTTP_BODY}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["fingerprint"])')"

echo "==> Deduplication"
expect "repeat signal accepted" POST /v1/analyze 200 "${SIGNAL}"

case "${HTTP_BODY}" in
*'"action":"suppressed"'*) pass "repeat signal suppressed (${FINGERPRINT})" ;;
*) fail "duplicate was not suppressed: ${HTTP_BODY}" ;;
esac

case "${HTTP_BODY}" in
*'"ai_invoked":false'*) pass "no second Gemini call" ;;
*) fail "Gemini was called again for a duplicate: ${HTTP_BODY}" ;;
esac

echo
echo "All checks passed."
