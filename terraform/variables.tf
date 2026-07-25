variable "project_id" {
  description = "Target GCP project id."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry and Vertex AI."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "name_prefix" {
  description = "Prefix applied to every named resource."
  type        = string
  default     = "sentinelai"
}

variable "container_image" {
  description = <<-EOT
    Fully-qualified container image. CI passes the immutable digest-or-SHA tag
    it just pushed, which keeps Terraform the single source of truth for what
    is actually running. Defaults to Google's sample image so a first apply
    succeeds before any build exists.
  EOT
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

# --- Application tuning -----------------------------------------------------

variable "gemini_model" {
  description = "Vertex AI model id used for triage."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "suppression_window_minutes" {
  description = "Repeat occurrences of a fingerprint inside this window are folded into the existing incident without a second AI call or page."
  type        = number
  default     = 30

  validation {
    condition     = var.suppression_window_minutes >= 1 && var.suppression_window_minutes <= 1440
    error_message = "suppression_window_minutes must be between 1 and 1440."
  }
}

variable "notify_min_severity" {
  description = "Lowest severity that is allowed to page a human."
  type        = string
  default     = "SEV3"

  validation {
    condition     = contains(["SEV1", "SEV2", "SEV3", "SEV4"], var.notify_min_severity)
    error_message = "notify_min_severity must be SEV1..SEV4."
  }
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook. Stored in Secret Manager, never in the image. Leave empty to disable notifications."
  type        = string
  default     = ""
  sensitive   = true
}

variable "log_filter" {
  description = "Cloud Logging filter deciding which entries enter the pipeline. Deliberately narrow — broadening it is the main way to increase cost."
  type        = string
  default     = <<-EOT
    severity>=ERROR
    AND resource.type=("cloud_run_revision" OR "gce_instance" OR "k8s_container" OR "cloud_function" OR "gcs_bucket" OR "cloudsql_database")
    AND NOT resource.labels.service_name="sentinelai-triage"
    AND NOT protoPayload.serviceName="cloudresourcemanager.googleapis.com"
  EOT
}

# --- Scaling / cost ---------------------------------------------------------

variable "min_instances" {
  description = "Cloud Run minimum instances. Keep at 0 to stay inside the free tier; 1 removes cold starts at roughly USD 9/month."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Upper bound on Cloud Run autoscaling — the hard ceiling on runaway spend during an alert storm."
  type        = number
  default     = 5
}

variable "digest_schedule" {
  description = "Cron for the reliability digest (in digest_timezone)."
  type        = string
  default     = "0 9 * * *"
}

variable "digest_timezone" {
  description = "IANA timezone for the digest schedule."
  type        = string
  default     = "Asia/Kolkata"
}

variable "log_retention_days" {
  description = "Lifecycle age at which archived digests and postmortems are deleted."
  type        = number
  default     = 90
}

# --- Optional features ------------------------------------------------------

variable "enable_budget_guard" {
  description = "Create a billing budget that publishes threshold breaches into the triage pipeline. Requires billing account permissions."
  type        = bool
  default     = false
}

variable "billing_account_id" {
  description = "Billing account id, required when enable_budget_guard is true."
  type        = string
  default     = ""
}

variable "monthly_budget_amount" {
  description = "Monthly budget in USD used by the budget guard."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Address that receives platform-health alerts (AI degraded, DLQ backlog, service 5xx). Empty means dashboard-only."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/repo allowed to impersonate the deployer service account via Workload Identity Federation. Empty disables keyless CI."
  type        = string
  default     = ""
}
