# Architecture & Design Decisions

This document records *why* the system is shaped the way it is. Anyone can list
services; the decisions and their trade-offs are what matter in a design review.

---

## Design principles

1. **Deterministic before probabilistic.** Fingerprinting is regex + SHA-256, not
   an embedding-similarity call. It is free, instant, reproducible and testable.
   The AI is used only for the part that genuinely needs judgement.
2. **The AI is enrichment, never a dependency.** Every path has a non-AI fallback.
3. **Cost is an architectural property, not an afterthought.** The ordering of
   pipeline stages *is* the cost model.
4. **The human is the scarcest resource.** Everything is recorded; almost nothing
   pages.

---

## ADR-001 · Deterministic fingerprinting over semantic clustering

**Context.** Deduplication needs to decide whether two errors are "the same".

**Options considered.**

| Approach | Cost per event | Deterministic | Testable |
|---|---|---|---|
| Embedding similarity | ~$0.0001 + latency | No | Poorly |
| LLM "are these the same?" | ~$0.001 + latency | No | Poorly |
| **Regex normalisation + hash** | **0** | **Yes** | **Yes** |

**Decision.** Normalise high-cardinality tokens (UUIDs, timestamps, IPs, pod
suffixes, durations, numbers) out of the message, then SHA-256 the remainder
alongside service and resource type.

**Consequences.** Free, instant, and unit-testable — `test_fingerprint.py` asserts
that four flavours of per-occurrence variance collapse. The trade-off is that
semantically-equivalent-but-textually-different errors (`connection refused` vs
`could not connect`) produce two incidents. That is the right failure direction:
over-splitting produces two pages, under-splitting hides a real second incident
behind an existing one.

Severity is deliberately *excluded* from the hash — the same bug flapping between
WARNING and ERROR is still one bug.

---

## ADR-002 · Suppression window before inference

**Context.** Gemini is the only component with meaningful per-event cost.

**Decision.** Order the pipeline so the Firestore lookup gates the model call:

```
fingerprint (0 cost) → Firestore read (~$0.000001) → [suppressed? return]
                                                   → Gemini (~$0.0003)
                                                   → page a human (priceless)
```

**Consequences.** A 4,000-line error burst costs one Gemini call instead of 4,000 —
a ~99.97% reduction in both AI spend and pages. The trade-off is that a genuine
escalation *within* the window (SEV3 becoming SEV1) is not re-evaluated until the
window closes. Mitigation is documented in the roadmap: re-run analysis when the
occurrence rate crosses a threshold, since a rate change is itself a signal.

Window length is a tunable (`suppression_window_minutes`), because the right value
is an operational judgement, not a constant.

---

## ADR-003 · Structured output via response schema, not prompt instruction

**Context.** The analysis is stored, metered, rendered and alerted on. Free-form
prose is unusable downstream.

**Decision.** Pass a JSON schema as `response_schema` with
`response_mime_type="application/json"`. The model is constrained at generation
time rather than asked politely.

**Consequences.** Parse failures effectively disappear, enums stay valid, and
`_to_analysis()` can coerce with confidence. The prompt is then free to carry only
*judgement* guidance — what SEV1 means, how to write remediation — rather than
formatting boilerplate. Retry-on-malformed-JSON logic disappears entirely.

---

## ADR-004 · Graceful degradation over hard dependency

**Context.** What happens when Vertex AI is quota-limited or down?

**Decision.** `heuristic_analysis()` classifies by log severity plus a keyword→
category table. It is deliberately conservative: it never assigns a severity
*lower* than the raw log severity implies, and escalates capacity/security signals.
Output is flagged `degraded=true`, counted in a log-based metric, and alerted on.

**Why it matters.** An observability platform that goes dark when a dependency goes
dark is worthless during exactly the incident you built it for. The correlated
failure — a regional event taking out both your workloads and Vertex AI — is the
scenario where triage matters most.

Readiness deliberately does **not** probe Vertex AI. Failing readiness on a
dependency the system is designed to survive would convert a degradation into a
full outage.

---

## ADR-005 · Push subscription over pull

| | Push | Pull |
|---|---|---|
| Compute | Scales to zero | Needs a running consumer |
| Auth | OIDC per request | Consumer holds credentials |
| Backpressure | Cloud Run concurrency + max instances | Manual |
| Cost at idle | $0 | Continuous |

**Decision.** Push. It is the only option that keeps a scale-to-zero service, and
it makes authentication per-request rather than ambient.

**Consequence.** The HTTP status code becomes the ack/nack contract, which must be
handled deliberately — see ADR-006.

---

## ADR-006 · Status codes as the delivery contract

Pub/Sub interprets the response, so status codes are load-bearing:

| Situation | Response | Reasoning |
|---|---|---|
| Triaged successfully | `200` | Ack |
| Malformed envelope / undecodable data | `200` | **Ack on purpose.** Retrying cannot fix it; redelivering a poison message burns quota until the DLQ policy fires |
| Firestore unavailable, quota exhausted | `503` | Nack — genuinely transient, retry with backoff |
| 5 consecutive failures | — | Dead-lettered, alert fires, message retained 7 days for replay |

Acking a malformed message *looks* like swallowing an error. It is the opposite:
the failure is logged and counted, but the delivery system is told the truth, which
is that retrying is pointless.

---

## ADR-007 · Firestore document id = fingerprint

**Decision.** Use the fingerprint as the document id rather than an auto-id with a
fingerprint field.

**Consequences.**

- **Idempotency for free.** Pub/Sub is at-least-once; duplicate delivery of the same
  log line lands on the same document.
- **No query needed** for the hot path — a direct `.get()`, which is the cheapest
  possible Firestore operation.
- **Atomic increments.** `firestore.Increment(1)` is server-side, so concurrent
  Cloud Run instances processing the same burst cannot lose counts to a
  read-modify-write race. This matters: during a storm, several instances *will*
  handle the same fingerprint simultaneously.

---

## ADR-008 · Workload Identity Federation, zero keys

**Context.** CI needs to deploy to GCP.

**Rejected.** A service account JSON key in a GitHub secret. Exported keys are the
most common root cause of real GCP compromises: they do not expire, they are copied
into local `.env` files, and their leakage is invisible until abused.

**Decision.** WIF with an attribute condition pinning the exact repository:

```hcl
attribute_condition = "assertion.repository == '${var.github_repository}'"
```

Without that condition, *any* GitHub repository could mint tokens for the project —
the classic confused-deputy misconfiguration. The SA binding is scoped to the same
`attribute.repository` principal set, so both layers must agree.

**Consequence.** There is no long-lived credential anywhere in this project.

---

## ADR-009 · Monitoring alerts as a triage *source*

**Decision.** A Pub/Sub notification channel routes alert policies onto the same
events topic the log sink writes to.

**Consequence.** Threshold alerts get fingerprinted, AI-triaged and deduplicated
exactly like log errors — one pipeline, three producers.

The important subtlety: **platform-health alerts do not use this channel.** The
"Vertex AI degraded", "DLQ backlog" and "triage service 5xx" policies route
straight to a human, because routing them into the pipeline would ask the triage
service to triage its own outage — precisely when it cannot. Likewise, the
`SEV1 triaged` policy notifies a human rather than republishing, which would loop.

---

## ADR-010 · Two-layer authorisation, scoped per endpoint

Cloud Run IAM (`roles/run.invoker`, no `allUsers`) is the primary gate. It
validates the OIDC token's signature **and its audience against the service URL**
before the request reaches the container.

The application adds a second, narrower check: is this the *specific* identity
that should be calling *this endpoint*? That is a question IAM cannot answer.

| Endpoint | Allowed caller | Rationale |
|---|---|---|
| `/v1/events/pubsub` | Pub/Sub invoker SA only | Nothing else should inject events |
| `/jobs/digest` | Cloud Scheduler SA only | Nothing else should trigger the job |
| `/v1/analyze`, `/v1/incidents` | any IAM-authorised identity | Operator-facing by design |
| `/healthz`, `/readyz` | unauthenticated | Probes run before IAM in the request path |

**This was originally wrong, and the mistake is instructive.** The first version
pinned *every* endpoint to the same list of three service accounts. The result:
the service deployed cleanly, Cloud Run reported it healthy, probes passed — and
every operator command failed. `make smoke` and `make demo` authenticate the
human via `gcloud auth print-identity-token`, and that identity was on no list,
so the allowlist rejected it with 403 after Cloud Run had already let it through.

Two lessons worth keeping:

- **An allowlist that excludes the operator is a broken allowlist.** Defence in
  depth that locks out legitimate use is not security, it is an outage.
- **The audience must not be pinned in-app on Cloud Run.** The service cannot
  reference its own URL at plan time, so `SENTINEL_EXPECTED_AUDIENCE` was never
  set and the check silently passed on everything. A check that cannot be
  configured correctly is worse than no check, because it reads as protection.
  Cloud Run performs it authoritatively; the setting now exists solely for
  deployments that are not behind Cloud Run.

Note also that `gcloud auth print-identity-token` for a *user* account mints a
token whose audience is gcloud's own OAuth client id
(`32555940559.apps.googleusercontent.com`), not the service URL. Any design that
requires a service-URL audience is therefore incompatible with a human operator
holding a user credential — another reason the audience check belongs to the
platform layer, not the application.

---

## Data model

```
incidents/{fingerprint}
├── fingerprint        "a3f8c21d9e4b7f60"
├── status             OPEN | REOPENED
├── occurrences        41                    ← atomic increment
├── first_seen         ISO-8601
├── last_seen          ISO-8601              ← drives the suppression window
├── service            "checkout-api"
├── source             LOG_SINK | MONITORING_ALERT | BUDGET_ALERT | MANUAL
├── sample_message     first 2000 chars
├── notified           bool
├── environment        "prod"
└── analysis
    ├── severity              SEV1..SEV4
    ├── category              AVAILABILITY | CAPACITY | SECURITY | ...
    ├── title
    ├── probable_root_cause
    ├── blast_radius
    ├── customer_impact
    ├── confidence            0.0–1.0
    ├── remediation[]         { description, command, requires_approval }
    ├── investigation_queries[]
    ├── is_actionable
    ├── model_used
    ├── latency_ms
    └── degraded              bool
```

Storing `latency_ms`, `model_used` and `degraded` on the document means AI
performance is queryable historically, not just in a metrics window — you can ask
"which model classified this incident, and was it degraded at the time?" six months
later.

---

## Failure modes and responses

| Failure | Detection | Response | User impact |
|---|---|---|---|
| Vertex AI unavailable | `degraded_triages` metric | Heuristic fallback | Lower-confidence triage, still paged |
| Firestore unavailable | `/readyz` 503, triage raises | `503` → Pub/Sub retries | Delayed, not lost (24h retention) |
| Slack webhook invalid | `slack_notify_failed` log | Incident still recorded | No page — mitigated by the email channel |
| Poison message | `pubsub_ingest_rejected` log | Acked and dropped | One signal lost, pipeline healthy |
| Alert storm | Cloud Run concurrency | `max_instances=5` ceiling, suppression absorbs the rest | Bounded spend |
| Triage service down | 5xx alert policy | Pub/Sub retries 24h, then DLQ | Recoverable by replaying the DLQ |
| Runaway cost | Billing budget → pipeline | Triaged as a COST incident | Early warning at 50% |

---

## Scaling characteristics

Today's numbers, and where they break:

| Dimension | Current | First bottleneck |
|---|---|---|
| Events/sec | ~200 (5 instances × 40 concurrency) | Raise `max_instances` |
| Distinct fingerprints | Unbounded | Firestore scales horizontally |
| Digest window | 200 incidents | Firestore query limit — page or pre-aggregate |
| Gemini calls | ~60/min | Vertex AI quota — request an increase or batch |

The suppression ratio improves as volume grows, because a bigger storm means more
duplicates per fingerprint. The system gets *more* efficient under load, which is
the opposite of most alerting pipelines.
