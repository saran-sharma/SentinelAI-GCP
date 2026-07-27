#!/usr/bin/env bash
#
# Deployment diagnostic for the "Ready revision, but /healthz returns Google's
# HTML 404" class of problem.
#
# The question this answers is: WHO generated the response? A request to a
# Cloud Run service traverses three hops, and only the last one is your app:
#
#     client -> Google Front End -> Cloud Run IAM -> container
#
# Startup and liveness probes skip the first two. They are executed by the
# Cloud Run infrastructure directly against the container port, so a probe
# passing proves the app serves /healthz and proves nothing whatsoever about
# the public path. This script inspects each hop separately.
#
# Usage: ./scripts/diagnose.sh <PROJECT_ID> [REGION]

set -uo pipefail # deliberately not -e: every probe should run even if one fails

PROJECT_ID="${1:?usage: diagnose.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE="sentinelai-triage"

# gcloud emits CRLF on Windows; an unstripped \r corrupts headers and URLs.
gc() { gcloud "$@" | tr -d '\r'; }

rule() { printf '\n=== %s %s\n' "$1" "$(printf '%.0s-' $(seq 1 $((62 - ${#1}))))"; }

# --- 1. What is actually deployed -------------------------------------------

rule "1. Deployed state"

URL="$(gc run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.url)' 2>/dev/null)"
REVISION="$(gc run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(status.latestReadyRevisionName)' 2>/dev/null)"
IMAGE="$(gc run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)' 2>/dev/null)"
INGRESS="$(gc run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='value(metadata.annotations."run.googleapis.com/ingress")' 2>/dev/null)"

echo "  url:      ${URL:-<unresolved>}"
echo "  revision: ${REVISION:-<none>}"
echo "  image:    ${IMAGE:-<none>}"
echo "  ingress:  ${INGRESS:-all (default)}"

if [ -z "${URL}" ]; then
  echo "  STOP: the service could not be described. Wrong project or region?"
  exit 1
fi

# Traffic split matters: a Ready revision that receives 0% of traffic is not
# the revision answering your requests.
rule "2. Traffic allocation"
gc run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='table(status.traffic[].revisionName, status.traffic[].percent, status.traffic[].latestRevision)' 2>/dev/null

# --- 3. Who answers, unauthenticated ----------------------------------------
#
# A private Cloud Run service rejects credential-less requests at the GFE. The
# body is Google's HTML error page, never FastAPI's {"detail":"Not Found"}.
# This is the single most common source of the reported symptom: a browser tab
# or a curl without the header never reaches the container.

rule "3. Unauthenticated GET /healthz (expect rejection at the GFE)"
curl -sS -o /tmp/diag_unauth.txt -D /tmp/diag_unauth_hdr.txt "${URL}/healthz"
echo "  status:       $(head -1 /tmp/diag_unauth_hdr.txt | tr -d '\r')"
echo "  server:       $(grep -i '^server:' /tmp/diag_unauth_hdr.txt | tr -d '\r')"
echo "  content-type: $(grep -i '^content-type:' /tmp/diag_unauth_hdr.txt | tr -d '\r')"
echo "  body (first 200 chars):"
head -c 200 /tmp/diag_unauth.txt | sed 's/^/    /'
echo

# --- 4. Token hygiene --------------------------------------------------------

rule "4. Identity token"
RAW_TOKEN="$(gcloud auth print-identity-token 2>/dev/null)"
TOKEN="$(printf '%s' "${RAW_TOKEN}" | tr -d '\r\n')"

if [ "${RAW_TOKEN}" != "${TOKEN}" ]; then
  echo "  WARNING: the raw token contained CR/LF (Windows gcloud)."
  echo "           Unstripped, it produces a malformed Authorization header and"
  echo "           the GFE treats the request as unauthenticated."
fi

echo "  account:  $(gc config get-value account 2>/dev/null)"
echo "  length:   ${#TOKEN}"
echo "  audience: $(printf '%s' "${TOKEN}" | cut -d. -f2 \
  | tr '_-' '/+' | { read -r p; printf '%s' "${p}$(printf '%*s' $(((4 - ${#p} % 4) % 4)) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("aud","?"))' 2>/dev/null || echo '<unparsed>')"

# --- 5. Who answers, authenticated ------------------------------------------

rule "5. Authenticated requests (same host, three paths)"
for path in /healthz /readyz /docs; do
  code="$(curl -sS -o /tmp/diag_body.txt -w '%{http_code}' \
    -H "Authorization: Bearer ${TOKEN}" "${URL}${path}")"
  ctype="$(curl -sS -o /dev/null -D - -H "Authorization: Bearer ${TOKEN}" "${URL}${path}" \
    | grep -i '^content-type:' | tr -d '\r' | cut -d' ' -f2-)"
  printf '  %-10s -> HTTP %-4s %-32s %s\n' "${path}" "${code}" "${ctype:-<none>}" \
    "$(head -c 60 /tmp/diag_body.txt | tr -d '\n')"
done

echo
echo "  Interpretation:"
echo "    All three 200            -> the platform is fine; earlier failures were client-side."
echo "    All three HTML 403/404   -> rejected at the GFE. The app is never reached."
echo "    /docs 200, /healthz HTML -> impossible on one host with one token; the two"
echo "                                requests were not sent the same way. Re-run both here."
echo "    JSON {\"detail\":...}      -> the response IS from FastAPI, so routing is fine."

# --- 6. Did the request reach the container? --------------------------------
#
# This is the decisive test. Cloud Run logs every request that reaches the
# service. If /healthz does not appear here, it was rejected upstream and no
# amount of application debugging will help.

rule "6. Cloud Run request log for /healthz (last 10 minutes)"
gc logging read \
  "resource.type=\"cloud_run_revision\"
   AND resource.labels.service_name=\"${SERVICE}\"
   AND httpRequest.requestUrl:\"/healthz\"" \
  --project="${PROJECT_ID}" --limit=5 --freshness=10m \
  --format='table(timestamp, httpRequest.status, httpRequest.requestMethod, httpRequest.requestUrl)' \
  2>/dev/null || echo "  (no request-log entries)"

echo
echo "  No rows = the request never reached the service. The 404 came from the"
echo "  Google Front End, not from FastAPI. Look at IAM (section 7), not at code."

# --- 7. Invoker IAM ----------------------------------------------------------

rule "7. run.invoker bindings"
gc run services get-iam-policy "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" \
  --format='table(bindings.role, bindings.members)' 2>/dev/null || echo "  (could not read policy)"

CALLER="$(gc config get-value account 2>/dev/null)"
echo
echo "  Caller: ${CALLER}"
echo "  If it is absent above and is not a Project Owner, the GFE rejects it"
echo "  before the container. Fix in terraform.tfvars:"
echo "      operator_members = [\"user:${CALLER}\"]"

# --- 8. Application-level evidence ------------------------------------------

rule "8. Application logs (last 10 minutes)"
gc logging read \
  "resource.type=\"cloud_run_revision\"
   AND resource.labels.service_name=\"${SERVICE}\"
   AND jsonPayload.message:*" \
  --project="${PROJECT_ID}" --limit=10 --freshness=10m \
  --format='value(timestamp, jsonPayload.message, jsonPayload.path, jsonPayload.status)' \
  2>/dev/null || echo "  (none)"

echo
echo "  'service_started' present = the app booted and its routes are registered."

rule "Done"
echo "Attach this entire output when reporting the problem."
