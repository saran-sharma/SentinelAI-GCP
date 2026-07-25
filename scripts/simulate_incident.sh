#!/usr/bin/env bash
#
# Demo driver. Publishes a realistic mix of production signals to the events
# topic and then shows what triage did with them.
#
# The point of the burst section is the headline number: N raw log lines
# collapse into a handful of incidents, with one Gemini call each.
#
# Usage: ./scripts/simulate_incident.sh <PROJECT_ID> [BURST_SIZE]

set -euo pipefail

PROJECT_ID="${1:?usage: simulate_incident.sh <PROJECT_ID> [BURST_SIZE]}"
BURST="${2:-40}"
TOPIC="sentinelai-events"

publish() {
  gcloud pubsub topics publish "${TOPIC}" \
    --project="${PROJECT_ID}" \
    --message="$1" >/dev/null
}

log_entry() {
  local service="$1" severity="$2" text="$3"
  cat <<EOF
{"severity":"${severity}","textPayload":"${text}","resource":{"type":"cloud_run_revision","labels":{"service_name":"${service}"}},"timestamp":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF
}

echo "==> 1/4 Distinct failure modes (each should create its own incident)"

publish "$(log_entry checkout-api ERROR \
  'psycopg2.OperationalError: connection pool exhausted, 0 of 20 connections available on db-primary')"

publish "$(log_entry image-worker CRITICAL \
  'container killed: OOMKilled, memory limit 512Mi exceeded while resizing asset')"

publish "$(log_entry ledger-api ERROR \
  'PermissionDenied: caller lacks roles/storage.objectViewer on bucket ledger-exports-prod')"

publish "$(log_entry notification-svc ERROR \
  'upstream deadline exceeded contacting payments-gateway after 30000ms')"

echo "==> 2/4 Deliberate noise (should be classified SEV4 and never page)"

publish "$(log_entry checkout-api WARNING \
  'DeprecationWarning: datetime.utcnow() is deprecated and will be removed')"

echo "==> 3/4 Burst of ${BURST} variations of one failure"
echo "        (different request ids, pods, durations — one fingerprint)"

for i in $(seq 1 "${BURST}"); do
  publish "$(log_entry checkout-api ERROR \
    "psycopg2.OperationalError: connection pool exhausted, request $(uuidgen 2>/dev/null || echo "req-${i}-$RANDOM") failed after $((RANDOM % 5000 + 1000))ms on pod checkout-api-7d4f9b8c6d-$(head -c3 /dev/urandom | base64 | tr -dc 'a-z0-9' | head -c5)")"
done

echo "==> 4/4 Waiting 30s for push delivery and triage"
sleep 30

SERVICE_URL="$(gcloud run services describe sentinelai-triage \
  --project="${PROJECT_ID}" --region="${REGION:-us-central1}" \
  --format='value(status.url)')"

cat >/tmp/sentinel_report.py <<'PYTHON'
import json
import sys

data = json.load(sys.stdin)
rows = data.get("incidents", [])
total = sum(int(r.get("occurrences", 1)) for r in rows)

summary = f"  {len(rows)} incidents from {total} raw signals"
if total:
    summary += f"  ({(1 - len(rows) / total) * 100:.0f}% noise absorbed)"
print(summary)
print()

for row in sorted(rows, key=lambda d: -int(d.get("occurrences", 1))):
    analysis = row.get("analysis", {})
    severity = str(analysis.get("severity", "?"))
    count = int(row.get("occurrences", 1))
    print(f"  [{severity:<4}] x{count:<4} {analysis.get('title', '')}")
    print(f"          cause: {str(analysis.get('probable_root_cause', ''))[:100]}")
PYTHON

echo
echo "==> Incidents recorded in the last hour:"
curl -sS "${SERVICE_URL}/v1/incidents?hours=1" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" |
  python3 /tmp/sentinel_report.py

echo
echo "==> Gemini was invoked once per distinct failure mode, not once per signal."
