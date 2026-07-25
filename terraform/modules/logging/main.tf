/**
 * Ingestion edge: a project-level log sink routes matching entries into the
 * events topic, plus log-based metrics that turn the service's own structured
 * logs into first-class monitoring signals.
 */

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "topic_id" { type = string }
variable "topic_name" { type = string }
variable "log_filter" { type = string }
variable "service_name" { type = string }

resource "google_logging_project_sink" "errors" {
  project     = var.project_id
  name        = "${var.name_prefix}-error-sink"
  description = "Routes production error logs into the SentinelAI triage pipeline."
  destination = "pubsub.googleapis.com/${var.topic_id}"
  filter      = var.log_filter

  # Terraform creates a dedicated writer identity for the sink.
  unique_writer_identity = true
}

resource "google_pubsub_topic_iam_member" "sink_writer" {
  project = var.project_id
  topic   = var.topic_name
  role    = "roles/pubsub.publisher"
  member  = google_logging_project_sink.errors.writer_identity
}

# --- Log-based metrics over the service's own structured logs ---------------
#
# The app emits `incident_triaged` / `incident_suppressed` as JSON. Promoting
# those to metrics gives SLO-able signals with zero extra instrumentation.

resource "google_logging_metric" "triaged" {
  project = var.project_id
  name    = "${var.name_prefix}/incidents_triaged"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    AND resource.labels.service_name="${var.service_name}"
    AND jsonPayload.message="incident_triaged"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"

    labels {
      key         = "severity"
      value_type  = "STRING"
      description = "Assigned incident severity"
    }
    labels {
      key         = "action"
      value_type  = "STRING"
      description = "created | reopened | ignored"
    }
  }

  label_extractors = {
    "severity" = "EXTRACT(jsonPayload.severity)"
    "action"   = "EXTRACT(jsonPayload.action)"
  }
}

resource "google_logging_metric" "suppressed" {
  project = var.project_id
  name    = "${var.name_prefix}/events_suppressed"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    AND resource.labels.service_name="${var.service_name}"
    AND jsonPayload.message="incident_suppressed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

# Distribution of AI latency, straight out of the triage log line.
resource "google_logging_metric" "ai_latency" {
  project = var.project_id
  name    = "${var.name_prefix}/ai_latency_ms"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    AND resource.labels.service_name="${var.service_name}"
    AND jsonPayload.message="incident_triaged"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  value_extractor = "EXTRACT(jsonPayload.ai_latency_ms)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 16
      growth_factor      = 2
      scale              = 100
    }
  }
}

# Fires whenever the heuristic fallback ran — i.e. Vertex AI was unavailable.
resource "google_logging_metric" "degraded" {
  project = var.project_id
  name    = "${var.name_prefix}/degraded_triages"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    AND resource.labels.service_name="${var.service_name}"
    AND jsonPayload.message="incident_triaged"
    AND jsonPayload.degraded=true
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

output "sink_writer_identity" { value = google_logging_project_sink.errors.writer_identity }
output "triaged_metric" { value = google_logging_metric.triaged.name }
output "suppressed_metric" { value = google_logging_metric.suppressed.name }
output "degraded_metric" { value = google_logging_metric.degraded.name }
output "ai_latency_metric" { value = google_logging_metric.ai_latency.name }
