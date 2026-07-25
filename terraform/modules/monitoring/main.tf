/**
 * Observability for the platform itself, plus the feedback loop that makes
 * Cloud Monitoring alerts a *source* for triage.
 *
 * The pubsub notification channel is the interesting part: an alert policy
 * firing publishes onto the same events topic the log sink writes to, so
 * threshold alerts get fingerprinted, AI-triaged and deduplicated exactly
 * like log errors. One pipeline, two producers.
 */

variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "service_name" { type = string }
variable "topic_id" { type = string }
variable "topic_name" { type = string }
variable "project_number" { type = string }
variable "dlq_subscription" { type = string }
variable "alert_email" { type = string }

locals {
  monitoring_agent = "serviceAccount:service-${var.project_number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
  metric_prefix    = "logging.googleapis.com/user/${var.name_prefix}"
  enable_email     = var.alert_email != ""
}

# --- Notification channels ---------------------------------------------------

resource "google_pubsub_topic_iam_member" "monitoring_publisher" {
  project = var.project_id
  topic   = var.topic_name
  role    = "roles/pubsub.publisher"
  member  = local.monitoring_agent
}

resource "google_monitoring_notification_channel" "pipeline" {
  project      = var.project_id
  display_name = "SentinelAI triage pipeline"
  type         = "pubsub"

  labels = {
    topic = var.topic_id
  }

  depends_on = [google_pubsub_topic_iam_member.monitoring_publisher]
}

resource "google_monitoring_notification_channel" "email" {
  count = local.enable_email ? 1 : 0

  project      = var.project_id
  display_name = "SentinelAI operator email"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }
}

locals {
  # Platform-health alerts go straight to a human. Routing them back into the
  # pipeline would mean the triage service is expected to triage its own
  # outage, which is exactly when it cannot.
  self_health_channels = local.enable_email ? [google_monitoring_notification_channel.email[0].id] : []
}

# --- Workload alerts (feed the pipeline) -------------------------------------

# Any *other* Cloud Run service in the project erroring becomes a triage input.
# This is the second producer: threshold alerts and log errors converge on one
# fingerprinted, deduplicated incident stream.
resource "google_monitoring_alert_policy" "workload_5xx" {
  project      = var.project_id
  display_name = "Workload · Cloud Run 5xx (routed to SentinelAI)"
  combiner     = "OR"
  severity     = "ERROR"

  documentation {
    content   = "Elevated 5xx on a monitored workload. Published to the SentinelAI events topic for AI triage."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "5xx rate on monitored services"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name!=\"${var.service_name}\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
        group_by_fields    = ["resource.label.service_name"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.pipeline.id]
  alert_strategy {
    auto_close = "3600s"
  }
}

resource "google_monitoring_alert_policy" "sev1_detected" {
  project      = var.project_id
  display_name = "SentinelAI · SEV1 incident triaged"
  combiner     = "OR"
  severity     = "CRITICAL"

  documentation {
    content   = "The triage engine classified a signal as SEV1. Check the Slack alert and #incidents."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "SEV1 incident created"
    condition_threshold {
      filter          = "metric.type=\"${local.metric_prefix}/incidents_triaged\" AND resource.type=\"cloud_run_revision\" AND metric.labels.severity=\"SEV1\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.self_health_channels
  alert_strategy {
    auto_close = "3600s"
  }
}

# --- Platform health alerts (page a human) -----------------------------------

resource "google_monitoring_alert_policy" "ai_degraded" {
  project      = var.project_id
  display_name = "SentinelAI · Vertex AI degraded (heuristic fallback active)"
  combiner     = "OR"
  severity     = "WARNING"

  documentation {
    content   = <<-EOT
      The triage service fell back to rules-based classification, meaning
      Vertex AI calls are failing. Triage is still running but confidence is
      low. Runbook: docs/runbook.md#vertex-ai-degraded
    EOT
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Degraded triages in the last 10 minutes"
    condition_threshold {
      filter          = "metric.type=\"${local.metric_prefix}/degraded_triages\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 3
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.self_health_channels
  alert_strategy {
    auto_close = "3600s"
  }
}

resource "google_monitoring_alert_policy" "dead_letter_backlog" {
  project      = var.project_id
  display_name = "SentinelAI · Messages in dead-letter queue"
  combiner     = "OR"
  severity     = "ERROR"

  documentation {
    content   = <<-EOT
      Events failed 5 delivery attempts and were dead-lettered — signals are
      being dropped. Inspect with:
      `gcloud pubsub subscriptions pull ${var.dlq_subscription} --limit=10 --auto-ack`
      Runbook: docs/runbook.md#dead-letter-backlog
    EOT
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Undelivered messages in DLQ"
    condition_threshold {
      filter          = "metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\" AND resource.type=\"pubsub_subscription\" AND resource.labels.subscription_id=\"${var.dlq_subscription}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = local.self_health_channels
}

resource "google_monitoring_alert_policy" "service_errors" {
  project      = var.project_id
  display_name = "SentinelAI · Triage service 5xx"
  combiner     = "OR"
  severity     = "ERROR"

  documentation {
    content   = "The triage service is returning 5xx, so Pub/Sub is retrying and the pipeline is backing up. Runbook: docs/runbook.md#triage-service-5xx"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "5xx responses"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.service_name}\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.self_health_channels
}

# --- Dashboard ---------------------------------------------------------------

resource "google_monitoring_dashboard" "sentinelai" {
  project = var.project_id

  dashboard_json = jsonencode({
    displayName = "SentinelAI — Incident Triage"
    mosaicLayout = {
      columns = 12
      tiles = [
        {
          width = 6, height = 4, xPos = 0, yPos = 0
          widget = {
            title = "Incidents triaged by severity"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"${local.metric_prefix}/incidents_triaged\" AND resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_SUM"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.severity"]
                    }
                  }
                }
                plotType = "STACKED_BAR"
              }]
            }
          }
        },
        {
          width = 6, height = 4, xPos = 6, yPos = 0
          widget = {
            title = "Events suppressed (noise absorbed)"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"${local.metric_prefix}/events_suppressed\" AND resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_SUM"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
                plotType = "LINE"
              }]
            }
          }
        },
        {
          width = 6, height = 4, xPos = 0, yPos = 4
          widget = {
            title = "Gemini triage latency (p50 / p95)"
            xyChart = {
              dataSets = [
                {
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"${local.metric_prefix}/ai_latency_ms\" AND resource.type=\"cloud_run_revision\""
                      aggregation = {
                        alignmentPeriod  = "300s"
                        perSeriesAligner = "ALIGN_PERCENTILE_50"
                      }
                    }
                  }
                  plotType = "LINE"
                },
                {
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"${local.metric_prefix}/ai_latency_ms\" AND resource.type=\"cloud_run_revision\""
                      aggregation = {
                        alignmentPeriod  = "300s"
                        perSeriesAligner = "ALIGN_PERCENTILE_95"
                      }
                    }
                  }
                  plotType = "LINE"
                }
              ]
            }
          }
        },
        {
          width = 3, height = 4, xPos = 6, yPos = 4
          widget = {
            title = "Degraded triages"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"${local.metric_prefix}/degraded_triages\" AND resource.type=\"cloud_run_revision\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_SUM"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
              thresholds = [{ value = 1, color = "YELLOW", direction = "ABOVE" }]
            }
          }
        },
        {
          width = 3, height = 4, xPos = 9, yPos = 4
          widget = {
            title = "Dead-lettered messages"
            scorecard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\" AND resource.type=\"pubsub_subscription\" AND resource.labels.subscription_id=\"${var.dlq_subscription}\""
                  aggregation = {
                    alignmentPeriod  = "300s"
                    perSeriesAligner = "ALIGN_MAX"
                  }
                }
              }
              thresholds = [{ value = 1, color = "RED", direction = "ABOVE" }]
            }
          }
        },
        {
          width = 12, height = 4, xPos = 0, yPos = 8
          widget = {
            title = "Cloud Run — request count by response class"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${var.service_name}\""
                    aggregation = {
                      alignmentPeriod    = "300s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                      groupByFields      = ["metric.label.response_code_class"]
                    }
                  }
                }
                plotType = "STACKED_AREA"
              }]
            }
          }
        }
      ]
    }
  })
}

output "dashboard_id" { value = google_monitoring_dashboard.sentinelai.id }
output "pipeline_channel_id" { value = google_monitoring_notification_channel.pipeline.id }
