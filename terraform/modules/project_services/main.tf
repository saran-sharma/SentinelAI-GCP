variable "project_id" { type = string }

variable "services" {
  type = list(string)
}

# disable_on_destroy is off on purpose: tearing down this stack must never
# disable an API another workload in the project depends on.
resource "google_project_service" "enabled" {
  for_each = toset(var.services)

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

output "enabled_services" {
  value = [for s in google_project_service.enabled : s.service]
}
