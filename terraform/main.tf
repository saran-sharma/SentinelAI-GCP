/**
 * SentinelAI — root module.
 *
 * Dependency order is mostly implicit through outputs. The one deliberately
 * awkward edge is Pub/Sub push: the subscription needs the Cloud Run URL, and
 * Cloud Run needs the invoker service accounts, so identity is created first,
 * then the service, then the subscription that targets it.
 */

locals {
  service_name = "${var.name_prefix}-triage"

  required_services = [
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "pubsub.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
}

module "project_services" {
  source = "./modules/project_services"

  project_id = var.project_id
  services   = var.enable_budget_guard ? concat(local.required_services, ["billingbudgets.googleapis.com"]) : local.required_services
}

module "iam" {
  source = "./modules/iam"

  project_id        = var.project_id
  name_prefix       = var.name_prefix
  github_repository = var.github_repository

  depends_on = [module.project_services]
}

module "artifact_registry" {
  source = "./modules/artifact_registry"

  project_id        = var.project_id
  region            = var.region
  name_prefix       = var.name_prefix
  runtime_sa_member = module.iam.runtime_sa_member

  depends_on = [module.project_services]
}

module "storage" {
  source = "./modules/storage"

  project_id        = var.project_id
  region            = var.region
  name_prefix       = var.name_prefix
  runtime_sa_member = module.iam.runtime_sa_member
  retention_days    = var.log_retention_days

  depends_on = [module.project_services]
}

module "secrets" {
  source = "./modules/secrets"

  project_id        = var.project_id
  region            = var.region
  name_prefix       = var.name_prefix
  runtime_sa_member = module.iam.runtime_sa_member
  slack_webhook_url = var.slack_webhook_url

  depends_on = [module.project_services]
}

# Firestore in Native mode. Free tier covers 1 GiB and 50k reads/day, which is
# comfortably more than this workload needs.
resource "google_firestore_database" "incidents" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  # Point-in-time recovery is a paid feature; deletion protection is not.
  delete_protection_state = var.environment == "prod" ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"

  depends_on = [module.project_services]
}

# Note: the digest query (`last_seen >= cutoff ORDER BY last_seen DESC`) filters
# and orders on a single field, so Firestore's automatic single-field index
# serves it. No composite index is required — adding one would just cost writes.

module "cloud_run" {
  source = "./modules/cloud_run"

  project_id       = var.project_id
  region           = var.region
  service_name     = local.service_name
  environment      = var.environment
  container_image  = var.container_image
  runtime_sa_email = module.iam.runtime_sa_email
  min_instances    = var.min_instances
  max_instances    = var.max_instances

  gemini_model               = var.gemini_model
  suppression_window_minutes = var.suppression_window_minutes
  notify_min_severity        = var.notify_min_severity
  artifacts_bucket           = module.storage.bucket_name
  slack_secret_id            = module.secrets.slack_secret_id

  # Application-level allowlist mirroring the IAM invoker bindings.
  allowed_invoker_sas = join(",", [
    module.iam.pubsub_invoker_email,
    module.iam.scheduler_sa_email,
    module.iam.deployer_sa_email,
  ])

  invoker_members = [
    module.iam.pubsub_invoker_member,
    module.iam.scheduler_sa_member,
    "serviceAccount:${module.iam.deployer_sa_email}",
  ]

  depends_on = [
    module.project_services,
    module.secrets,
    google_firestore_database.incidents,
  ]
}

module "pubsub" {
  source = "./modules/pubsub"

  project_id    = var.project_id
  name_prefix   = var.name_prefix
  push_endpoint = "${module.cloud_run.service_url}/v1/events/pubsub"
  push_sa_email = module.iam.pubsub_invoker_email
  audience      = module.cloud_run.service_url

  depends_on = [module.project_services]
}

module "logging" {
  source = "./modules/logging"

  project_id   = var.project_id
  name_prefix  = var.name_prefix
  topic_id     = module.pubsub.topic_id
  topic_name   = module.pubsub.topic_name
  log_filter   = var.log_filter
  service_name = local.service_name
}

module "monitoring" {
  source = "./modules/monitoring"

  project_id       = var.project_id
  name_prefix      = var.name_prefix
  service_name     = local.service_name
  topic_id         = module.pubsub.topic_id
  topic_name       = module.pubsub.topic_name
  project_number   = module.pubsub.project_number
  dlq_subscription = "${var.name_prefix}-events-dlq-hold"
  alert_email      = var.alert_email

  depends_on = [module.logging]
}

module "scheduler" {
  source = "./modules/scheduler"

  project_id         = var.project_id
  region             = var.region
  name_prefix        = var.name_prefix
  service_url        = module.cloud_run.service_url
  scheduler_sa_email = module.iam.scheduler_sa_email
  schedule           = var.digest_schedule
  timezone           = var.digest_timezone

  depends_on = [module.cloud_run]
}

module "budget" {
  source = "./modules/budget"
  count  = var.enable_budget_guard ? 1 : 0

  billing_account_id = var.billing_account_id
  project_id         = var.project_id
  name_prefix        = var.name_prefix
  topic_id           = module.pubsub.topic_id
  topic_name         = module.pubsub.topic_name
  monthly_amount     = var.monthly_budget_amount
  project_number     = module.pubsub.project_number
}
