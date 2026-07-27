variable "project_id" { type = string }
variable "region" { type = string }
variable "service_name" { type = string }
variable "environment" { type = string }
variable "container_image" { type = string }
variable "runtime_sa_email" { type = string }
variable "min_instances" { type = number }
variable "max_instances" { type = number }
variable "gemini_model" { type = string }
variable "suppression_window_minutes" { type = number }
variable "notify_min_severity" { type = string }
variable "artifacts_bucket" { type = string }
variable "slack_secret_id" { type = string }
variable "pubsub_invoker_sa" { type = string }
variable "scheduler_sa" { type = string }
variable "invoker_members" { type = list(string) }

resource "google_cloud_run_v2_service" "triage" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

  # Private by default. Nothing on the internet can reach this URL; only
  # identities holding roles/run.invoker (granted below) can.
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = var.runtime_sa_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    # One request at a time would burn instances during a storm; 40 lets a
    # single instance absorb a burst of push deliveries, most of which exit
    # on the cheap suppression path.
    max_instance_request_concurrency = 40
    timeout                          = "120s"

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        # CPU is only allocated while a request is in flight — the difference
        # between a few cents and a few dollars a month at this traffic level.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "SENTINEL_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "SENTINEL_REGION"
        value = var.region
      }
      env {
        name  = "SENTINEL_ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "SENTINEL_SERVICE_NAME"
        value = var.service_name
      }
      env {
        name  = "SENTINEL_MODEL_NAME"
        value = var.gemini_model
      }
      env {
        name  = "SENTINEL_SUPPRESSION_WINDOW_MINUTES"
        value = tostring(var.suppression_window_minutes)
      }
      env {
        name  = "SENTINEL_NOTIFY_MIN_SEVERITY"
        value = var.notify_min_severity
      }
      env {
        name  = "SENTINEL_ARTIFACTS_BUCKET"
        value = var.artifacts_bucket
      }
      env {
        name  = "SENTINEL_VERIFY_OIDC"
        value = "true"
      }
      # Machine endpoints are pinned to one service account each. Operator
      # endpoints deliberately have no allowlist — Cloud Run IAM already
      # decides who may reach the service at all, and pinning them locked the
      # human operator out of /v1/analyze and /v1/incidents entirely.
      env {
        name  = "SENTINEL_PUBSUB_INVOKER_SA"
        value = var.pubsub_invoker_sa
      }
      env {
        name  = "SENTINEL_SCHEDULER_SA"
        value = var.scheduler_sa
      }

      # SENTINEL_EXPECTED_AUDIENCE is intentionally unset. Cloud Run validates
      # the token audience against the service URL before forwarding, and the
      # service cannot reference its own URL at plan time. Set it only when
      # running this image somewhere other than Cloud Run.

      # Injected from Secret Manager at start-up. The value never appears in
      # the image, the Terraform plan output, or `gcloud run services describe`.
      env {
        name = "SENTINEL_SLACK_WEBHOOK_URL"
        value_source {
          secret_key_ref {
            secret  = var.slack_secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
        initial_delay_seconds = 3
        period_seconds        = 5
        failure_threshold     = 6
        timeout_seconds       = 3
      }

      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 5
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    # Revision-scoped labels churn on every deploy and produce noisy diffs.
    ignore_changes = [template[0].labels, client, client_version]
  }
}

# Explicit invoker grants — no allUsers, no allAuthenticatedUsers.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invoker_members)

  project  = var.project_id
  location = google_cloud_run_v2_service.triage.location
  name     = google_cloud_run_v2_service.triage.name
  role     = "roles/run.invoker"
  member   = each.value
}

output "service_url" { value = google_cloud_run_v2_service.triage.uri }
output "service_name" { value = google_cloud_run_v2_service.triage.name }
output "latest_revision" { value = google_cloud_run_v2_service.triage.latest_ready_revision }
