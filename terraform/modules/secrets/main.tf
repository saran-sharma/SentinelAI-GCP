variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "runtime_sa_member" { type = string }

variable "slack_webhook_url" {
  type      = string
  sensitive = true
}

locals {
  has_webhook = var.slack_webhook_url != ""
}

resource "google_secret_manager_secret" "slack_webhook" {
  project   = var.project_id
  secret_id = "${var.name_prefix}-slack-webhook"

  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

# Placeholder when no webhook is supplied, so the Cloud Run secret mount always
# resolves. Rotating the real value out-of-band (gcloud / console) does not
# require a Terraform apply — see docs/runbook.md.
resource "google_secret_manager_secret_version" "slack_webhook" {
  secret      = google_secret_manager_secret.slack_webhook.id
  secret_data = local.has_webhook ? var.slack_webhook_url : "disabled"
}

# secretAccessor only — the runtime can read the current value and nothing else.
resource "google_secret_manager_secret_iam_member" "runtime_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.slack_webhook.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = var.runtime_sa_member
}

output "slack_secret_id" { value = google_secret_manager_secret.slack_webhook.secret_id }
output "slack_secret_version" { value = google_secret_manager_secret_version.slack_webhook.version }
