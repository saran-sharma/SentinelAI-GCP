/**
 * Identity: one service account per job function, each with the narrowest
 * role set that job needs, plus keyless GitHub Actions via Workload Identity
 * Federation.
 *
 * There is no service account key anywhere in this stack. That is the single
 * highest-value security control here — exported keys are the most common
 * root cause of real GCP compromises, and WIF removes the need for one.
 */

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "github_repository" { type = string }

locals {
  # WIF is only created when a repository is pinned. A pool without an
  # attribute condition would let *any* GitHub repo mint tokens for this
  # project — the classic confused-deputy misconfiguration.
  enable_wif = var.github_repository != ""
}

# --- Runtime identity (Cloud Run) -------------------------------------------

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-run"
  display_name = "SentinelAI Cloud Run runtime"
  description  = "Triage service: reads Vertex AI, writes Firestore/GCS/metrics."
}

resource "google_project_iam_member" "runtime" {
  for_each = toset([
    "roles/aiplatform.user",         # call Gemini
    "roles/datastore.user",          # Firestore incident store
    "roles/monitoring.metricWriter", # custom metrics
    "roles/logging.logWriter",       # structured logs
    "roles/cloudtrace.agent",        # trace correlation
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.runtime.member
}

# --- Caller identities -------------------------------------------------------

resource "google_service_account" "pubsub_invoker" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-pubsub"
  display_name = "SentinelAI Pub/Sub push invoker"
  description  = "Mints OIDC tokens for push delivery to Cloud Run."
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-scheduler"
  display_name = "SentinelAI Cloud Scheduler invoker"
  description  = "Invokes the digest job endpoint."
}

# --- CI/CD identity ----------------------------------------------------------

resource "google_service_account" "deployer" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-deployer"
  display_name = "SentinelAI GitHub Actions deployer"
  description  = "Builds images and applies Terraform from CI."
}

resource "google_project_iam_member" "deployer" {
  for_each = toset([
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/storage.admin",
    "roles/pubsub.admin",
    "roles/cloudscheduler.admin",
    "roles/monitoring.editor",
    "roles/logging.configWriter",
    "roles/secretmanager.admin",
    "roles/datastore.owner",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
  ])

  project = var.project_id
  role    = each.value
  member  = google_service_account.deployer.member
}

resource "google_iam_workload_identity_pool" "github" {
  count = local.enable_wif ? 1 : 0

  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-gh-pool"
  display_name              = "GitHub Actions"
  description               = "Keyless OIDC federation for GitHub Actions."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count = local.enable_wif ? 1 : 0

  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Belt and braces: the provider itself refuses tokens from other repos, and
  # the SA binding below is scoped to the same repository.
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  count = local.enable_wif ? 1 : 0

  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

# --- Outputs -----------------------------------------------------------------

output "runtime_sa_email" { value = google_service_account.runtime.email }
output "runtime_sa_member" { value = google_service_account.runtime.member }
output "pubsub_invoker_email" { value = google_service_account.pubsub_invoker.email }
output "pubsub_invoker_member" { value = google_service_account.pubsub_invoker.member }
output "scheduler_sa_email" { value = google_service_account.scheduler.email }
output "scheduler_sa_member" { value = google_service_account.scheduler.member }
output "deployer_sa_email" { value = google_service_account.deployer.email }

output "workload_identity_provider" {
  description = "Value for the GitHub Actions auth step."
  value       = local.enable_wif ? google_iam_workload_identity_pool_provider.github[0].name : ""
}
