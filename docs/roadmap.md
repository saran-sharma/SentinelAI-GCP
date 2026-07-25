# Roadmap — from portfolio project to enterprise platform

Honest gaps, ordered by value per hour of work. Being able to articulate what's
missing and why it matters is worth as much in an interview as what's built.

---

## Tier 1 · Highest value (1–3 hours each)

### Accuracy feedback loop

**The gap.** The system reports confidence but has no ground truth. There is no way
to answer "how often is the severity right?" — the question that gates every
further investment in automation.

**Build.** Slack interactive buttons (`✅ correct` / `⬆️ too low` / `⬇️ too high`)
posting to a new `/v1/feedback` endpoint, written back to the incident document.
Surface accuracy per category in the digest.

**Why first.** It converts opinion into a metric, and creates a labelled dataset
that makes everything below it evaluable.

### Golden-dataset evaluation in CI

**The gap.** AI tests use fakes. They prove the plumbing and the fallback; they
prove nothing about the prompt. A prompt edit could silently degrade classification
and CI would stay green.

**Build.** 30–50 realistic log samples with expected severity and category in
`tests/golden/`. A nightly workflow runs them against the real model and fails if
accuracy drops below a threshold. Keep it out of PR CI — cost and flakiness.

### Tiered model escalation

**The gap.** Flash handles everything, including the SEV1 calls where accuracy
matters most.

**Build.** Run Flash first; re-run with Pro when `confidence < 0.6` or severity
comes back SEV1. Record which tier decided, so the cost/accuracy trade-off is
measurable rather than assumed.

### Rate-based re-analysis

**The gap.** A fixed time window means an incident escalating *inside* the window —
SEV3 becoming a full outage — isn't re-evaluated until it closes.

**Build.** Track occurrences-per-minute on the document. Break suppression and
re-analyse when the rate jumps beyond a multiple of its baseline. A rate change is
itself a signal, and this is the most important correctness gap in the current
design.

---

## Tier 2 · Production hardening (half a day each)

### Multi-environment promotion

Terraform workspaces or per-environment directories for dev/staging/prod, with
plan-on-PR, apply-on-merge, and required approvals on prod. Today there is one
environment and `environment` is a label.

### Incident lifecycle and correlation

Incidents only open and reopen. Add acknowledge, assign and resolve via Slack
actions; auto-resolve when a fingerprint goes quiet for N hours; and correlate
incidents that start within a short window across dependent services into a single
parent — the "one root cause, five symptoms" case that still pages five times.

### Postmortem generation

`ArtifactStore.postmortem_path()` already exists but nothing writes to it. On
resolution of a SEV1/SEV2, generate a draft postmortem from the incident timeline,
occurrence data and remediation history. Draft, never final — the analysis is a
starting point for a human, not a substitute.

### Terraform drift detection

A scheduled workflow running `terraform plan -detailed-exitcode`; any drift is
published to the events topic and triaged as a CONFIGURATION incident by the same
pipeline. Closes the loop between IaC and the incident system, and is directly
relevant to cloud governance roles.

### VPC Service Controls & private networking

Cloud Run behind Direct VPC egress with a perimeter around Firestore, GCS and
Vertex AI. Required for regulated environments, and the answer to "how do you stop
data exfiltration if the service is compromised?".

---

## Tier 3 · Scale and enterprise integration

| Area | Work | Driver |
|---|---|---|
| **Multi-project** | Aggregate sinks at folder/org level, project label on every incident | Real orgs have dozens of projects |
| **PagerDuty / Opsgenie** | Replace the Slack-only gate with a real paging provider, honour schedules and escalation policies | Slack is not a pager |
| **Auto-remediation** | Execute low-risk, reversible remediation (scale up, restart revision) behind a policy allowlist and full audit trail | The obvious next step — and the one that needs the accuracy metric first |
| **Cost anomaly detection** | Query the BigQuery billing export daily, have Gemini explain deltas against forecast | Turns the budget guard from threshold-based to trend-based |
| **Knowledge base grounding** | RAG over past incidents and internal runbooks so remediation cites what actually worked here before | Biggest quality jump available; needs Vector Search |
| **SLO burn-rate integration** | Ingest SLO burn rate as a producer and weight severity by remaining error budget | Ties severity to a business contract rather than a heuristic |
| **Kubernetes/GKE producer** | Event exporter for pod crashloops, evictions, node pressure | Extends coverage to container platforms |

---

## Known limitations

Be upfront about these — pretending they don't exist is the failure mode.

1. **No accuracy measurement.** Confidence is self-reported by the model, which is
   not the same as being right.
2. **Fixed suppression window.** Time-based, not rate-based. See Tier 1.
3. **Single region.** Cloud Run, Firestore and Vertex AI all in one region. A
   regional outage takes the platform down alongside the workloads it watches.
4. **English-language logs assumed.** Fingerprint normalisation patterns are
   tuned for English error text.
5. **Digest caps at 200 incidents.** Beyond that it needs pre-aggregated rollups.
6. **No incident resolution.** Documents stay `OPEN` until they age out; there is
   no MTTR measurement because there is no close event.
7. **Slack is the only notification channel.** No paging provider, no escalation
   policy, no on-call schedule awareness.
8. **Firestore in Native mode without PITR.** Point-in-time recovery is a paid
   feature and is off; incident data loss is recoverable only from log replay.

---

## Deliberately out of scope

Things that would make it *bigger* without making it *better*:

- **A web UI.** The value is in the pipeline. A React dashboard would be the most
  visible and least interesting part, and Cloud Monitoring already renders the data.
- **Multi-cloud.** AWS/Azure ingestion would dilute the depth of GCP integration
  that makes this credible.
- **Custom model fine-tuning.** Prompt engineering with a schema gets you most of
  the way; fine-tuning without an accuracy metric is guessing expensively.
- **Kubernetes deployment.** Cloud Run is the correct choice for this workload.
  Running it on GKE to look sophisticated would be an anti-pattern I'd have to
  defend in an interview.
