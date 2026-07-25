/**
 * Event backbone.
 *
 *   log sink / alert policy / budget  ->  events topic
 *   events topic  --push+OIDC-->  Cloud Run /v1/events/pubsub
 *   5 failed attempts             ->  dead-letter topic (kept 7 days)
 *
 * The dead-letter topic is the difference between "a bug costs one message"
 * and "a bug costs a redelivery loop until someone notices the bill".
 */

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "push_endpoint" { type = string }
variable "push_sa_email" { type = string }
variable "audience" { type = string }

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_topic" "events" {
  project = var.project_id
  name    = "${var.name_prefix}-events"

  message_retention_duration = "86600s" # 24h replay window
}

resource "google_pubsub_topic" "dead_letter" {
  project = var.project_id
  name    = "${var.name_prefix}-events-dlq"

  message_retention_duration = "604800s" # 7 days to investigate
}

resource "google_pubsub_subscription" "push_to_run" {
  project = var.project_id
  name    = "${var.name_prefix}-events-push"
  topic   = google_pubsub_topic.events.id

  ack_deadline_seconds       = 60
  message_retention_duration = "86600s"
  expiration_policy {
    ttl = "" # never expire
  }

  push_config {
    push_endpoint = var.push_endpoint

    # Pub/Sub mints an OIDC token for this SA; Cloud Run IAM validates it
    # before the request reaches the container.
    oidc_token {
      service_account_email = var.push_sa_email
      audience              = var.audience
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  depends_on = [google_pubsub_topic_iam_member.dlq_publisher]
}

# The Pub/Sub service agent needs both of these to move a message to the DLQ:
# publish on the dead-letter topic, and subscribe on the source subscription.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dlq_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.push_to_run.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

# Retains dead-lettered messages so they can be inspected and replayed.
resource "google_pubsub_subscription" "dead_letter_hold" {
  project = var.project_id
  name    = "${var.name_prefix}-events-dlq-hold"
  topic   = google_pubsub_topic.dead_letter.id

  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  expiration_policy {
    ttl = ""
  }
}

output "topic_id" { value = google_pubsub_topic.events.id }
output "topic_name" { value = google_pubsub_topic.events.name }
output "dead_letter_topic_name" { value = google_pubsub_topic.dead_letter.name }
output "subscription_name" { value = google_pubsub_subscription.push_to_run.name }
output "project_number" { value = data.google_project.this.number }
