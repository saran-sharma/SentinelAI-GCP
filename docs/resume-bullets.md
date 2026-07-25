# Resume & Interview Talking Points

Pick 3–4 bullets, not all of them. Numbers come from the demo run — re-measure on
your own deployment and use your real figures.

---

## Primary bullets (pick 3–4)

> **Built an event-driven AIOps incident-triage platform on GCP** (Cloud Run,
> Pub/Sub, Firestore, Vertex AI Gemini) that ingests Cloud Logging, Cloud Monitoring
> and billing signals, and **cut alert volume by 89%** through deterministic
> fingerprinting and time-windowed suppression — collapsing 45 raw signals into 5
> actionable incidents.

> **Reduced LLM inference cost ~90%** by ordering the pipeline so a free SHA-256
> fingerprint and a sub-cent Firestore lookup gate every Gemini call, keeping
> steady-state platform cost under **$1/month** on GCP free tier.

> **Provisioned the entire platform as code** with Terraform across **11 reusable
> modules** (Cloud Run, Pub/Sub with dead-letter, log sinks, Secret Manager,
> Artifact Registry, IAM, monitoring dashboards and alert policies), with remote
> GCS state and CI-enforced `fmt`/`validate`/Checkov scanning.

> **Eliminated all long-lived cloud credentials** by implementing GitHub Actions →
> GCP **Workload Identity Federation** with repository-pinned attribute conditions,
> alongside four least-privilege service accounts and private Cloud Run with
> two-layer OIDC authorisation.

> **Engineered graceful degradation for the AI dependency** — a rules-based
> classifier keeps triage running when Vertex AI is unavailable, with degraded
> results flagged, metered via log-based metrics and alerted on, so the incident
> pipeline survives the correlated failure it exists to handle.

> **Instrumented full observability for the platform itself**: structured JSON logs
> with trace correlation, 4 log-based metrics, custom Cloud Monitoring metrics, a
> 6-widget dashboard and 5 alert policies, each linked to a written runbook procedure.

## Secondary bullets

> **Designed idempotent at-least-once event processing** using Firestore
> fingerprint-keyed documents and server-side atomic increments, eliminating
> duplicate pages from Pub/Sub redelivery and race conditions across concurrent
> Cloud Run instances.

> **Automated daily reliability reporting** via Cloud Scheduler and Gemini —
> aggregating 24h incident data into ranked failure modes with toil-elimination
> recommendations, archived to lifecycle-managed Cloud Storage.

> **Hardened an LLM against prompt injection from attacker-controlled log content**
> using delimiter fencing, explicit untrusted-data instruction, payload truncation
> and schema-constrained output that cannot emit executable directives.

> **Built a 57-test suite requiring no cloud credentials**, covering fingerprint
> collapse behaviour, suppression economics, AI retry/fallback policy and Pub/Sub
> ack-nack contract semantics.

---

## Skills this project evidences

| Category | Demonstrated by |
|---|---|
| **GCP** | Cloud Run, Pub/Sub, Firestore, Cloud Storage, Secret Manager, Artifact Registry, Cloud Scheduler, Cloud Logging, Cloud Monitoring, Vertex AI, IAM, Billing Budgets |
| **IaC** | Terraform 11 modules, remote state, validation, `for_each`, conditional resources, module composition |
| **CI/CD** | GitHub Actions, WIF, immutable SHA tags, smoke-test gating, automatic rollback |
| **AI/ML** | Vertex AI Gemini, structured output schemas, prompt engineering, injection hardening, graceful degradation, cost optimisation |
| **SRE** | Severity taxonomy, alert fatigue reduction, runbooks, dead-letter handling, idempotency, blast-radius control |
| **Security** | Zero-key auth, least privilege, secret management, non-root containers, IaC scanning, image scanning |
| **Observability** | Structured logging, trace correlation, log-based metrics, custom metrics, dashboards, alert policies |
| **Cost** | Scale-to-zero, model tier selection, source-side filtering, lifecycle policies, budget guardrails |

---

## Positioning against your Azure background

Your experience is Azure/AKS/Terraform/Azure DevOps. This project's job is to prove
the concepts transfer — say so explicitly rather than hoping they infer it.

| You already know | This shows | The line to use |
|---|---|---|
| Azure Monitor + Log Analytics + KQL | Cloud Logging, log sinks, log-based metrics, Logs Explorer queries | "Same telemetry pipeline shape — I mapped KQL-driven analysis onto Cloud Logging's filter language and log-based metrics" |
| Azure Alerts + Action Groups | Alert policies + Pub/Sub notification channels | "Action groups and notification channels solve the same routing problem; the GCP version let me route alerts back into the pipeline as a producer" |
| Terraform on Azure | Terraform on GCP, 11 modules | "Terraform is the constant — what changed was the provider and the IAM model" |
| Azure DevOps Pipelines | GitHub Actions with WIF | "Same pipeline stages; WIF is GCP's equivalent of Azure workload identity federation, and I used it for the same reason — no stored secrets" |
| AKS + Docker | Cloud Run, multi-stage non-root images | "I chose Cloud Run over GKE deliberately — the workload is bursty and doesn't justify a control plane" |
| Azure Key Vault | Secret Manager | "Same pattern: secret injected at runtime, rotation without redeployment" |
| Production support / on-call | The entire problem statement | "This project is the thing I wished existed on every on-call rotation I've done" |

**The framing that lands in an interview:**

> "Three years of production support taught me that the bottleneck isn't detection —
> it's the volume of undifferentiated alerts and the time spent working out which
> ones matter. I built this on GCP to prove the platform concepts transfer, and to
> attack the problem I actually lived with rather than a tutorial one."

---

## STAR stories

**Cost optimisation under constraint**

- **S** — Every event needed AI analysis to be useful, but per-event LLM calls at
  production log volume would cost more than the workload being monitored.
- **T** — Deliver AI-quality triage while keeping the platform inside free tier.
- **A** — Profiled the cost of each pipeline stage and reordered by cost:
  deterministic fingerprinting (free) → Firestore lookup (~$0.000001) → Gemini
  (~$0.0003) → human page. Suppression is a Firestore document check before any
  inference happens.
- **R** — 89% fewer AI calls on the demo burst, ~$0.15/month steady state, and the
  efficiency *improves* as volume grows because storms have more duplicates.

**Designing for correlated failure**

- **S** — The initial design treated Vertex AI as a hard dependency in the request
  path.
- **T** — Make the platform survive an AI outage, which is likeliest during a
  regional event — exactly when triage matters most.
- **A** — Built a rules-based classifier as fallback, deliberately conservative so
  it never under-pages. Flagged degraded results, added a log-based metric and alert
  policy. Deliberately excluded Vertex AI from the readiness probe so a survivable
  degradation can't fail the whole service out of rotation.
- **R** — Triage continues through Vertex AI outages with visible degradation
  rather than silence; the failure mode is "lower confidence", not "no incidents".

**Security by architecture**

- **S** — CI needed deploy-level access to a GCP project.
- **T** — Avoid the service account key pattern, the most common root cause of real
  GCP compromises.
- **A** — Implemented Workload Identity Federation with an attribute condition
  pinning the exact repository, closing the confused-deputy hole where any repo can
  mint tokens. Split runtime, invoker, scheduler and deployer identities so each
  holds only its own roles.
- **R** — Zero long-lived credentials in the project. Nothing to rotate, leak, or
  commit — the credential class was removed rather than managed.
