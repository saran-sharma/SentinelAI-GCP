# Runbook

Operational procedures for every alert this stack fires. Each alert policy's
`documentation` field links back to the matching section here.

---

## Quick reference

```bash
export PROJECT_ID=your-project
export REGION=us-central1
export SERVICE=sentinelai-triage

# Recent triage decisions
gcloud logging read \
  "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${SERVICE}\" \
   AND jsonPayload.message=\"incident_triaged\"" \
  --project="${PROJECT_ID}" --limit=20 --freshness=1h \
  --format='table(timestamp, jsonPayload.severity, jsonPayload.service, jsonPayload.action)'

# What is the service doing right now
gcloud run services describe "${SERVICE}" --region="${REGION}" --format=yaml

# Pipeline backlog
gcloud pubsub subscriptions describe sentinelai-events-push --format='value(name)'
```

---

## Vertex AI degraded

**Alert:** `SentinelAI · Vertex AI degraded (heuristic fallback active)`
**Meaning:** More than 3 triages in 5 minutes used the rules-based fallback. Triage
is still running, but classifications are low-confidence and marked `[degraded]`.

**Impact:** Incidents are still detected and paged — severity may be less accurate,
and no root-cause narrative is produced.

### Diagnose

```bash
# What is the actual error?
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND jsonPayload.message=\"vertex_ai_triage_failed\"" \
  --project="${PROJECT_ID}" --limit=10 --freshness=1h --format='value(jsonPayload.error)'
```

| Error contains | Cause | Action |
|---|---|---|
| `429`, `quota` | Vertex AI quota exhausted | Request a quota increase, or raise `suppression_window_minutes` to cut call volume |
| `403`, `permission` | Runtime SA lost `roles/aiplatform.user` | `terraform apply` to restore IAM |
| `404`, `model not found` | Model id retired or wrong region | Update `gemini_model`; confirm availability in `region` |
| `503`, `deadline` | Vertex AI regional degradation | Check the GCP status dashboard and wait — fallback is working as designed |

### Mitigate

```bash
# Immediate relief: widen the suppression window to cut call volume
terraform -chdir=terraform apply -var="project_id=${PROJECT_ID}" \
  -var="suppression_window_minutes=120"
```

### Verify recovery

```bash
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND jsonPayload.degraded=true" \
  --project="${PROJECT_ID}" --freshness=15m --limit=5
```
No results means healthy.

---

## Dead-letter backlog

**Alert:** `SentinelAI · Messages in dead-letter queue`
**Meaning:** Events failed 5 delivery attempts. **Signals are being dropped.**

### Diagnose

```bash
# Inspect without consuming
gcloud pubsub subscriptions pull sentinelai-events-dlq-hold \
  --project="${PROJECT_ID}" --limit=5 --format=json

# Why did delivery fail?
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND severity>=ERROR" \
  --project="${PROJECT_ID}" --limit=20 --freshness=1h
```

Common causes:

1. **Triage service returning 503** — Firestore unreachable, or a code bug. Follow
   [Triage service 5xx](#triage-service-5xx).
2. **Message shape the normaliser cannot handle** — should ack as `dropped` rather
   than dead-letter, so this indicates a bug in `ingest.py`. Capture the payload.
3. **OIDC misconfiguration** — Cloud Run rejecting the push identity with 403.
   Check that `pubsub_invoker_member` still holds `roles/run.invoker`.

### Replay after the fix

```bash
# Confirm health first
./scripts/smoke_test.sh "${PROJECT_ID}" "${REGION}"

# Move dead-lettered messages back onto the events topic
gcloud pubsub subscriptions pull sentinelai-events-dlq-hold \
  --project="${PROJECT_ID}" --limit=100 --format=json --auto-ack \
  | python3 -c '
import base64, json, subprocess, sys
for m in json.load(sys.stdin):
    data = base64.b64decode(m["message"]["data"]).decode()
    subprocess.run(["gcloud", "pubsub", "topics", "publish",
                    "sentinelai-events", "--message", data], check=True)
'
```

Replay is safe: fingerprint-keyed documents make reprocessing idempotent.

---

## Triage service 5xx

**Alert:** `SentinelAI · Triage service 5xx`
**Meaning:** The service is failing requests, so Pub/Sub is retrying and the
pipeline is backing up.

### Diagnose

```bash
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND severity>=ERROR" \
  --project="${PROJECT_ID}" --limit=20 --freshness=30m

gcloud run revisions list --service="${SERVICE}" --region="${REGION}" --limit=5
```

| Symptom | Likely cause | Action |
|---|---|---|
| `readiness_failed` | Firestore unreachable or API disabled | `gcloud services enable firestore.googleapis.com` |
| Started right after a deploy | Bad revision | Roll back (below) |
| `429` from Cloud Run | Hit `max_instances` | Raise the ceiling, understanding the cost implication |
| Container fails to start | Bad image or missing secret | Check the secret exists and the runtime SA can read it |

### Roll back

```bash
PREVIOUS=$(gcloud run revisions list --service="${SERVICE}" --region="${REGION}" \
  --filter="status.conditions.type=Ready AND status.conditions.status=True" \
  --sort-by="~metadata.creationTimestamp" --format="value(metadata.name)" --limit=2 | tail -n1)

gcloud run services update-traffic "${SERVICE}" --region="${REGION}" \
  --to-revisions="${PREVIOUS}=100"
```

Then re-apply Terraform with the known-good image tag so IaC matches reality —
a rollback that only exists in `gcloud` will be undone by the next apply.

---

## SEV1 incident triaged

**Alert:** `SentinelAI · SEV1 incident triaged`
**Meaning:** The engine classified a signal as customer-facing. This is the system
working, not failing.

1. Open the Slack alert — it carries root cause, blast radius and remediation.
2. Treat AI remediation as a **hypothesis**, not an instruction. Steps marked
   `requires_approval: true` mutate production state.
3. Run the `investigation_queries` in Logs Explorer to confirm before acting.
4. If the classification was wrong, note it — see [Tuning](#tuning) below.

---

## Alert storm / unexpected cost

### Immediate containment

```bash
# Stop ingestion at the source — the sink, not the service
gcloud logging sinks update sentinelai-error-sink \
  --project="${PROJECT_ID}" \
  --log-filter='severity>=CRITICAL AND resource.type="cloud_run_revision"'
```

This is the fastest lever: it cuts volume before Pub/Sub, Cloud Run or Vertex AI
are involved. Then widen the suppression window and, if necessary, disable
notifications while you investigate:

```bash
terraform -chdir=terraform apply -var="project_id=${PROJECT_ID}" \
  -var="suppression_window_minutes=240" \
  -var="notify_min_severity=SEV1"
```

### Investigate

```bash
# Which service is generating the volume?
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND jsonPayload.message=\"incident_suppressed\"" \
  --project="${PROJECT_ID}" --freshness=1h --format='value(jsonPayload.service)' \
  | sort | uniq -c | sort -rn | head
```

Revert the filter and window once the source is fixed.

---

## Rotating the Slack webhook

No Terraform apply required — Cloud Run reads `latest` at start-up.

```bash
echo -n "https://hooks.slack.com/services/NEW" \
  | gcloud secrets versions add sentinelai-slack-webhook --data-file=- --project="${PROJECT_ID}"

# Restart to pick it up
gcloud run services update "${SERVICE}" --region="${REGION}" \
  --update-env-vars="ROTATED_AT=$(date +%s)"

# Disable the old version once verified
gcloud secrets versions disable <OLD_VERSION> --secret=sentinelai-slack-webhook
```

---

## Tuning

| Symptom | Knob | Direction |
|---|---|---|
| Too many pages for the same issue | `suppression_window_minutes` | Increase |
| Escalations detected too slowly | `suppression_window_minutes` | Decrease |
| Noise still reaching Slack | `notify_min_severity` | `SEV3` → `SEV2` |
| Real issues classified SEV4 | `SYSTEM_INSTRUCTION` in `app/ai/prompts.py` | Add the counter-example |
| Related errors split into separate incidents | `_NORMALISERS` in `app/fingerprint.py` | Add a pattern — **and a test** |
| Vertex AI spend too high | `suppression_window_minutes`, `log_filter` | Increase / narrow |

Every fingerprint change needs a test in `test_fingerprint.py`. That file is the
specification for what "the same incident" means; changing behaviour without
changing the spec is how dedup logic rots.

---

## Troubleshooting deployment

### Every path returns 404, but the revision is Ready

The service is almost certainly still running the placeholder image. `var.container_image`
defaults to `us-docker.pkg.dev/cloudrun/container/hello` so the first `terraform apply`
succeeds before any image exists, and that container serves `/` only — every other
path, including `/healthz`, returns 404.

```bash
gcloud run services describe "${SERVICE}" --region="${REGION}" \
  --format='value(spec.template.spec.containers[0].image)'
```

If it contains `cloudrun/container/hello`, build and deploy the real image:

```bash
make deploy PROJECT_ID="${PROJECT_ID}"
```

`make smoke` now checks this first and fails with an explicit message.

### 401 `invalid OIDC token`

The response includes the underlying reason. Read it first:

```bash
gcloud logging read \
  "resource.labels.service_name=\"${SERVICE}\" AND jsonPayload.message=\"oidc_verification_failed\"" \
  --project="${PROJECT_ID}" --limit=5 --freshness=1h --format='value(jsonPayload.error)'
```

| Reason contains | Cause | Fix |
|---|---|---|
| `Token expired` | Identity tokens live ~1 hour | Re-run `gcloud auth print-identity-token` |
| `Wrong number of segments` | An access token was sent, not an identity token | Use `print-identity-token`, not `print-access-token` |
| `Token has wrong audience` | `SENTINEL_EXPECTED_AUDIENCE` is set on Cloud Run | Unset it — Cloud Run already validates the audience |
| `Could not fetch certs` | No egress to `www.googleapis.com` | Check VPC egress / firewall |

### 403 `caller ... is not permitted on /v1/events/pubsub`

Correct behaviour: that endpoint is pinned to the Pub/Sub invoker service
account. A human cannot post to it directly — use `/v1/analyze`, which is the
operator-facing equivalent, or publish to the topic:

```bash
gcloud pubsub topics publish sentinelai-events --message='{"severity":"ERROR","textPayload":"test"}'
```

### 403 from Cloud Run before reaching the app

The caller has no `roles/run.invoker`. Project Owners inherit it; anyone else
needs an explicit grant:

```hcl
operator_members = ["user:you@example.com"]
```

### Container serves nothing locally — connection reset or empty reply

`curl: (56) Recv failure` / `curl: (52) Empty reply from server` from the host
means the TCP connection was accepted and then dropped. With rootless Podman the
port forwarder accepts on the host before checking whether anything is listening
inside the container, which produces exactly this. Check, in order:

```bash
# 1. Was the port published at all?
podman ps --format '{{.Names}}\t{{.Ports}}'

# 2. Is the app listening inside the container, on the right interface?
podman exec -it <container> python -c "import socket;s=socket.socket();print(s.connect_ex(('127.0.0.1',8080)))"
#    0 = listening. Anything else = the process is not up on that port.

# 3. Did the process bind to 0.0.0.0, not 127.0.0.1?
#    A process bound to 127.0.0.1 inside the container is unreachable from the host.
podman exec -it <container> ss -ltnp 2>/dev/null || true
```

Run the image the way Cloud Run does, and curl the published port:

```bash
podman run --rm -p 8080:8080 -e PORT=8080 -e SENTINEL_VERIFY_OIDC=false \
  us-central1-docker.pkg.dev/sentinelai-gcp/sentinelai/sentinelai-triage:$(git rev-parse --short HEAD)

curl -i http://127.0.0.1:8080/healthz    # use 127.0.0.1, not localhost
```

Prefer `127.0.0.1` over `localhost`: the server binds IPv4 only, and `localhost`
resolves to `::1` first on many images. That failure mode reports
`curl: (7) Failed to connect`, which is distinct from the reset above — the two
are different problems and worth telling apart.

## Manual operations

```bash
# Trigger the digest now
gcloud scheduler jobs run sentinelai-daily-digest --location="${REGION}"

# Triage an arbitrary signal
curl -sS -X POST "$(gcloud run services describe ${SERVICE} --region=${REGION} --format='value(status.url)')/v1/analyze" \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H "Content-Type: application/json" \
  -d '{"service":"manual","severity":"ERROR","text":"paste the error here"}' | jq

# Clear an incident so the next occurrence is treated as new
gcloud firestore documents delete \
  "projects/${PROJECT_ID}/databases/(default)/documents/incidents/<FINGERPRINT>"

# Read archived digests
gcloud storage ls "gs://${PROJECT_ID}-sentinelai-artifacts/digests/**"
```
