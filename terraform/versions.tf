terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.14"
    }
  }

  # Remote state with locking. Bootstrapped by scripts/bootstrap.sh, which
  # creates the bucket with versioning before the first apply.
  #
  #   terraform init -backend-config="bucket=<PROJECT_ID>-tfstate"
  backend "gcs" {
    prefix = "sentinelai"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    app        = "sentinelai"
    managed_by = "terraform"
    env        = var.environment
  }
}
