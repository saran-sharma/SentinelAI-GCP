variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "runtime_sa_member" { type = string }

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  description   = "SentinelAI container images"
  format        = "DOCKER"

  docker_config {
    immutable_tags = false
  }

  # Registry storage is billed by GB and old CI images accumulate fast. Keep
  # enough history to roll back, delete the rest automatically.
  cleanup_policies {
    id     = "keep-recent-releases"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-stale-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "runtime_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = var.runtime_sa_member
}

output "repository_id" { value = google_artifact_registry_repository.images.repository_id }
output "registry_host" { value = "${var.region}-docker.pkg.dev" }
output "image_base" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}" }
