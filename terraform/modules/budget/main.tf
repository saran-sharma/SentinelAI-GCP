/**
 * Cost guardrail. Budget threshold breaches are published to the same events
 * topic as logs and alerts, so "we are at 90% of budget" is triaged, deduped
 * and delivered by the same machinery as "the database is down".
 *
 * Optional because it needs billing-account-level permissions that a personal
 * free-tier account may not grant to the deployer service account.
 */

variable "billing_account_id" { type = string }
variable "project_id" { type = string }
variable "name_prefix" { type = string }
variable "topic_id" { type = string }
variable "topic_name" { type = string }
variable "monthly_amount" { type = number }
variable "project_number" { type = string }

resource "google_pubsub_topic_iam_member" "billing_publisher" {
  project = var.project_id
  topic   = var.topic_name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:billing-budgets@system.gserviceaccount.com"
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account_id
  display_name    = "${var.name_prefix}-monthly"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_amount)
    }
  }

  # Early warning at 50%, action at 90%, and a forecast rule that fires before
  # the money is actually spent.
  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.9
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    pubsub_topic   = var.topic_id
    schema_version = "1.0"
  }

  depends_on = [google_pubsub_topic_iam_member.billing_publisher]
}

output "budget_name" { value = google_billing_budget.monthly.display_name }
