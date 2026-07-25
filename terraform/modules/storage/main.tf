variable "project_id" { type = string }
variable "region" { type = string }
variable "name_prefix" { type = string }
variable "runtime_sa_member" { type = string }
variable "retention_days" { type = number }

resource "google_storage_bucket" "artifacts" {
  project  = var.project_id
  name     = "${var.project_id}-${var.name_prefix}-artifacts"
  location = var.region

  # Every access goes through IAM. No ACLs, no public objects, ever.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Digests are audit evidence — keep the history, but not forever.
  versioning { enabled = true }

  lifecycle_rule {
    condition { age = var.retention_days }
    action { type = "Delete" }
  }

  lifecycle_rule {
    condition {
      age                = 30
      with_state         = "ARCHIVED"
      num_newer_versions = 3
    }
    action { type = "Delete" }
  }

  # Nearline after a month: digests are read in the first days and archived
  # thereafter, so the storage class should follow the access pattern.
  lifecycle_rule {
    condition { age = 30 }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }
}

# objectAdmin, not admin: the service writes and reads objects but must never
# be able to reconfigure or delete the bucket itself.
resource "google_storage_bucket_iam_member" "runtime_writer" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = var.runtime_sa_member
}

output "bucket_name" { value = google_storage_bucket.artifacts.name }
output "bucket_url" { value = google_storage_bucket.artifacts.url }
