output "service_url" {
  description = "Cloud Run URL of the triage API (private — requires an OIDC token)."
  value       = module.cloud_run.service_url
}

output "service_name" {
  value = module.cloud_run.service_name
}

output "events_topic" {
  description = "Pub/Sub topic every producer publishes to."
  value       = module.pubsub.topic_name
}

output "dead_letter_topic" {
  value = module.pubsub.dead_letter_topic_name
}

output "artifacts_bucket" {
  description = "GCS bucket holding archived digests and postmortems."
  value       = module.storage.bucket_name
}

output "image_base" {
  description = "Artifact Registry path CI pushes to."
  value       = module.artifact_registry.image_base
}

output "runtime_service_account" {
  value = module.iam.runtime_sa_email
}

output "deployer_service_account" {
  description = "Set as the GCP_SERVICE_ACCOUNT GitHub secret."
  value       = module.iam.deployer_sa_email
}

output "workload_identity_provider" {
  description = "Set as the GCP_WIF_PROVIDER GitHub secret. Empty when github_repository is unset."
  value       = module.iam.workload_identity_provider
}

output "dashboard_url" {
  description = "Cloud Monitoring dashboard for the triage pipeline."
  value       = "https://console.cloud.google.com/monitoring/dashboards/builder/${basename(module.monitoring.dashboard_id)}?project=${var.project_id}"
}

output "log_sink_writer_identity" {
  description = "Sink identity granted publish rights on the events topic."
  value       = module.logging.sink_writer_identity
}

output "smoke_test_command" {
  description = "One-liner to prove the deployment works end to end."
  value       = <<-EOT
    curl -s -X POST "${module.cloud_run.service_url}/v1/analyze" \
      -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
      -H "Content-Type: application/json" \
      -d '{"service":"checkout-api","severity":"ERROR","text":"psycopg2.OperationalError: connection pool exhausted"}' | jq
  EOT
}
