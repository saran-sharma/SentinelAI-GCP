# Interview Preparation

Twenty questions you will actually be asked about this project, with answers
grounded in the code. Every answer points at a file you can open on screen.

---

## Architecture & design

### 1. Walk me through the architecture in two minutes.

> Three producers — Cloud Logging via a sink, Cloud Monitoring alert policies via a
> Pub/Sub notification channel, and Cloud Billing budget notifications — all publish
> to one Pub/Sub topic. A push subscription delivers to a private Cloud Run service
> with an OIDC token.
>
> The service normalises whichever shape arrived into a single event model, computes
> a deterministic fingerprint, and checks Firestore. If that fingerprint was seen
> inside the suppression window, it does an atomic increment and returns — no AI
> call, no page. Otherwise it calls Gemini with an enforced JSON response schema to
> get severity, root cause, blast radius and remediation commands, stores the
> incident, and pages Slack only if severity clears the gate.
>
> Cloud Scheduler triggers a daily digest that asks Gemini to find recurring failure
> modes and recommend the automation that would eliminate them, archived to GCS.
>
> Everything is Terraform across eleven modules, deployed by GitHub Actions using
> Workload Identity Federation — there is no service account key in the project.

### 2. Why does the suppression check come before the AI call?

> Because ordering *is* the cost model. Fingerprinting is free, a Firestore read is
> a millionth of a cent, Gemini is roughly $0.0003 per call, and paging a human is
> the most expensive thing the system can do. Sorting stages by cost means a
> 4,000-line error burst costs one Gemini call and one page instead of 4,000 of each.
>
> The trade-off I accepted: an escalation *inside* the window — SEV3 becoming SEV1 —
> isn't re-evaluated until it closes. The fix is in the roadmap: re-run analysis when
> the occurrence rate crosses a threshold, because a rate change is itself a signal.

### 3. Why deterministic fingerprinting instead of embeddings?

> I considered embedding similarity and asking an LLM "are these the same?". Both
> cost money per event, are non-deterministic, and are hard to unit test — and I'd
> be putting the probabilistic thing on the hot path *before* the cheap filter.
>
> Regex normalisation plus SHA-256 is free, instant and fully testable.
> `test_fingerprint.py` proves that request ids, pod suffixes, IPs, durations and
> timestamps collapse while genuinely different errors stay distinct.
>
> The cost is that `connection refused` and `could not connect` produce two
> incidents. That's the right direction to fail: over-splitting costs an extra page,
> under-splitting *hides a real second incident* behind an existing one.

### 4. Why Cloud Run rather than GKE or Cloud Functions?

> The workload is bursty and event-driven: nothing for hours, then a spike. Cloud
> Run scales to zero, so idle cost is genuinely zero, and with `cpu_idle = true` I
> only pay while a request is in flight.
>
> GKE means paying for a control plane and nodes 24/7 to handle a workload that's
> idle most of the day, plus I'd own node upgrades and capacity planning for no
> benefit at this scale. Cloud Functions would work, but I wanted a real HTTP
> service with several routes, middleware and dependency injection — that's an
> application, not a function, and Cloud Run lets me keep it containerised and
> portable.

### 5. Why Firestore and not Cloud SQL or Memorystore?

> Three reasons, all specific to the access pattern.
>
> Using the fingerprint as the document id gives idempotency for free — Pub/Sub is
> at-least-once, and a duplicate delivery lands on the same document instead of
> paging twice. The hot path is a single `.get()` by key, the cheapest operation
> Firestore offers, with no query planning. And `firestore.Increment(1)` is a
> server-side atomic operation, so concurrent Cloud Run instances handling the same
> burst can't lose counts to a read-modify-write race — which *will* happen during a
> storm.
>
> Cloud SQL would mean paying for an always-on instance and managing connection
> pooling from a scale-to-zero service — the exact anti-pattern the demo incident
> is about. Memorystore has the same always-on cost plus no durability, and these
> incidents are audit records.

---

## AI integration

### 6. How do you stop the model returning malformed JSON?

> I don't ask it for JSON — I constrain it. The call passes a `response_schema` with
> `response_mime_type="application/json"`, so structure is enforced at generation
> time. Enums stay valid, required fields are present, and I deleted an entire class
> of retry-on-parse-failure logic.
>
> That also frees the prompt to carry only judgement — what SEV1 means, how to write
> remediation — instead of formatting boilerplate.

### 7. What happens when Vertex AI is down?

> Triage keeps running. `heuristic_analysis()` classifies by log severity plus a
> keyword-to-category table. It's deliberately conservative — it never assigns a
> severity lower than the raw log severity implies, and escalates capacity and
> security signals — because under-paging during an AI outage is the worst possible
> failure mode.
>
> The output is flagged `degraded: true`, counted in a log-based metric, and an alert
> fires so we know we're running blind.
>
> The detail I'd point at in a design review: readiness deliberately does *not*
> probe Vertex AI. Failing readiness on a dependency you're designed to survive
> turns a degradation into a full outage.

### 8. How do you handle prompt injection? Log content is attacker-influenceable.

> That's exactly right, and it's the threat I designed for — anyone who can get a
> string into your logs can get it into the model's context.
>
> Three controls. The payload is fenced in delimiters and explicitly labelled
> untrusted data. The system instruction says never to follow instructions inside it,
> and to classify anything that looks like a directive as a possible log-injection
> SECURITY finding. And `max_log_chars` truncates at 6,000 characters, bounding both
> token spend and the volume of attacker-controlled text.
>
> The structural control matters most though: the model's output is a *classification*
> constrained by a schema. It can't emit arbitrary instructions, and nothing it
> returns is executed. Remediation commands are rendered to a human with
> `requires_approval` flags — they are never run automatically. There's a test,
> `test_untrusted_log_content_is_fenced_in_the_prompt`, asserting the fencing stays.

### 9. Why Flash instead of Pro?

> Cost and latency, and the task doesn't need Pro. Triage is structured
> classification with a constrained output schema, not open-ended reasoning. Flash
> is roughly 20× cheaper and noticeably faster, which matters when this sits inline
> on an incident path.
>
> The enhancement I'd make for production is tiered escalation: run Flash first, and
> re-run with Pro only when confidence is below a threshold or severity comes back
> SEV1 — spend the money where the stakes justify it. That's in the roadmap.

### 10. How do you know the AI is any good?

> Honestly — right now I don't measure accuracy, and that's the biggest gap. I store
> `confidence`, `model_used`, `latency_ms` and `degraded` on every incident so the
> performance data is queryable historically, but there's no ground truth to compare
> against.
>
> The roadmap entry is a feedback loop: Slack buttons for "correct severity" /
> "wrong severity", written back to Firestore. That gives a labelled dataset, which
> gives an accuracy metric per category, which is what you'd need before trusting
> auto-remediation.

---

## Infrastructure & IaC

### 11. Walk me through your Terraform structure.

> Eleven modules, one per concern — IAM, Pub/Sub, Cloud Run, logging, monitoring,
> and so on — with a root module wiring them through outputs. State is in a
> versioned GCS bucket; versioning is non-negotiable because it's the only recovery
> path from a corrupted state write.
>
> Dependency order is mostly implicit through outputs. The one awkward edge is
> Pub/Sub push: the subscription needs the Cloud Run URL, and Cloud Run needs the
> invoker service accounts, so IAM is created first, then the service, then the
> subscription targeting it.
>
> A couple of decisions I'd defend: `disable_on_destroy = false` on API enablement,
> because tearing down this stack must never disable an API another workload
> depends on. And the container image is a variable that CI passes as an immutable
> SHA tag, so Terraform stays the source of truth for what's actually running —
> rollback is just re-applying an older tag.

### 12. How does CI/CD authenticate without keys?

> Workload Identity Federation. GitHub Actions requests an OIDC token, GCP exchanges
> it for short-lived credentials via a workload identity pool.
>
> The critical line is the attribute condition:
> `assertion.repository == 'owner/repo'`. Without it, *any* GitHub repository could
> mint tokens for my project — that's the confused-deputy misconfiguration you see
> in real breaches. The service account binding is scoped to the same repository
> principal set, so both layers have to agree.
>
> The result is that there's no long-lived credential anywhere in this project. No
> key to rotate, no key to leak, no key to accidentally commit.

### 13. Someone gets read access to your repo. What can they do?

> Nothing, and that's by design. There are no credentials in the repository —
> `terraform.tfvars` is gitignored, the Slack webhook lives in Secret Manager, and
> CI uses federation, so the only "secret" in GitHub is a pointer to a WIF provider
> that only works from an OIDC token issued to that specific repository.
>
> They'd learn my architecture, which I consider acceptable — it's a portfolio
> project, and security by obscurity isn't a control.

### 14. How do you prevent a runaway bill?

> Layered limits. `max_instances = 5` is a hard ceiling on Cloud Run scaling, so an
> alert storm can't autoscale into a large bill. The log sink filters at the source,
> so non-actionable entries never cost a delivery. Suppression means duplicate
> signals never reach Gemini. Artifact Registry cleanup policies delete stale
> images. GCS transitions to Nearline at 30 days and deletes at 90.
>
> And there's an optional billing budget that publishes threshold breaches *into
> the triage pipeline* — so a cost anomaly gets fingerprinted, AI-triaged and
> Slack-notified exactly like an outage, with early warning at 50%.

---

## Reliability & operations

### 15. Pub/Sub is at-least-once. How do you handle duplicates?

> The Firestore document id is the fingerprint, so duplicate delivery of the same
> log line lands on the same document and increments a counter rather than creating
> a second incident or sending a second page. Idempotency falls out of the data
> model rather than needing explicit dedup logic.
>
> The increment is server-side and atomic, which matters because during a burst
> several Cloud Run instances genuinely do process the same fingerprint
> concurrently.

### 16. Why return 200 on a malformed message? Isn't that swallowing errors?

> It's the opposite — it's telling the delivery system the truth.
>
> The status code is Pub/Sub's ack/nack contract. A 5xx means "retry me". If I
> nacked a malformed payload, Pub/Sub would redeliver it with backoff until the
> dead-letter policy fired — burning quota and log volume on something that can
> never succeed no matter how many times we try.
>
> So malformed input acks with a 200 and a `dropped` reason, and it's logged and
> counted. Genuinely transient failures — Firestore unavailable, quota exhausted —
> return 503, which is what you actually want retried. Five failures dead-letters
> the message, an alert fires, and it's retained for 7 days so it can be replayed
> after a fix. Replay is safe because processing is idempotent.

### 17. How do you monitor the monitoring system?

> With its own primitives, which is the honest answer to "who watches the watchmen".
>
> The service emits structured JSON to stdout with Cloud Logging's field names, so
> logs arrive already parsed and trace-correlated. Four log-based metrics promote
> those log lines to first-class signals — incidents triaged by severity, events
> suppressed, AI latency as a distribution, and degraded triages. Plus custom
> metrics written directly, and a dashboard.
>
> Five alert policies cover it: SEV1 triaged, Vertex AI degraded, DLQ backlog, the
> service's own 5xx, and workload 5xx.
>
> The important routing decision: platform-health alerts go straight to a human,
> *not* into the pipeline. Routing them through triage would ask the service to
> triage its own outage — precisely when it can't.

### 18. What breaks first as this scales?

> Vertex AI quota, at around 60 calls a minute. Cloud Run capacity is a config
> change; quota needs a request or a batching strategy.
>
> The second bottleneck is the digest query, capped at 200 incidents — beyond that
> I'd pre-aggregate daily rollups instead of scanning.
>
> The interesting property is that the suppression ratio *improves* with volume: a
> bigger storm means more duplicates per fingerprint, so the system gets more
> efficient under load. That's the opposite of most alerting pipelines, where
> volume and cost scale together.

---

## Reflection

### 19. What would you do differently?

> Three things.
>
> I'd add the accuracy feedback loop from day one — I can tell you the system
> classified 41 events as SEV1, but not whether it was *right*, and that's the
> question that matters.
>
> I'd reconsider the fixed suppression window. Time-based is simple but crude; a
> rate-based trigger — re-analyse when occurrences per minute jumps — would catch
> escalations the current design sleeps through.
>
> And I'd have built the golden-dataset test earlier: a fixed set of realistic log
> samples with expected severities, run against the real model in CI. Right now my
> AI tests use fakes, which proves the plumbing and the fallback but not the prompt.

### 20. What's the single most interesting decision here?

> Making the AI optional.
>
> The obvious way to build this is "logs go to Gemini, Gemini decides". That system
> fails completely the moment Vertex AI has a bad day — and if it's a regional
> event, it'll be having that bad day at exactly the same time as the workloads
> you're monitoring. Correlated failure.
>
> So the AI enriches a pipeline that already works without it. Fingerprinting,
> deduplication, severity floors, routing and storage are all deterministic. Gemini
> makes the output *better*, not *possible*. That's the difference between a
> product with AI in it and an AI demo — and it's the principle I'd carry into any
> production system that puts a model on a critical path.

---

## Questions to ask them

- How do you currently handle alert fatigue, and what's your page-to-actionable ratio?
- Where has AI been introduced into your operational tooling, and where has it been
  deliberately kept out?
- What does the boundary look like between platform engineering and product teams
  for on-call ownership?
- How do you measure the reliability of the reliability tooling itself?

---

## Demo checklist

Have these open in tabs before the call:

1. **The architecture diagram** in the README — lead with this.
2. **`app/triage.py`** — the ordering comment at the top is the whole cost argument.
3. **`test_triage.py::test_burst_of_identical_events_calls_ai_once`** — the
   assertion that proves the claim.
4. **`app/ai/analyzer.py`** — `heuristic_analysis()` for the resilience story.
5. **`terraform/modules/iam/main.tf`** — the WIF attribute condition.
6. **The Cloud Monitoring dashboard** — live data beats slides.
7. **`make demo` output** — the noise-reduction number.
