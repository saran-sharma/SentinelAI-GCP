variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "service_url" { type = string }
variable "scheduler_sa_email" { type = string }
variable "schedule" { type = string }
variable "timezone" { type = string }

resource "google_cloud_scheduler_job" "digest" {
  project     = var.project_id
  region      = var.region
  name        = "${var.name_prefix}-daily-digest"
  description = "Generates the AI reliability digest and archives it to GCS."
  schedule    = var.schedule
  time_zone   = var.timezone

  # The digest is a read-mostly aggregation; a slow Firestore scan should not
  # trigger a duplicate run.
  attempt_deadline = "320s"

  retry_config {
    retry_count          = 2
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
  }

  http_target {
    http_method = "POST"
    uri         = "${var.service_url}/jobs/digest?window_hours=24"

    headers = {
      "Content-Type" = "application/json"
    }

    # Keyless: Scheduler mints an OIDC token for its own service account.
    oidc_token {
      service_account_email = var.scheduler_sa_email
      audience              = var.service_url
    }
  }
}

output "job_name" { value = google_cloud_scheduler_job.digest.name }
