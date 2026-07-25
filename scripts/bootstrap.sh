#!/usr/bin/env bash
#
# One-time project bootstrap: enables the APIs Terraform itself needs, and
# creates the versioned GCS bucket that holds remote state.
#
# Everything else is Terraform's job. This script exists only because state
# storage cannot be managed by the state it stores.
#
# Usage: ./scripts/bootstrap.sh <PROJECT_ID> [REGION]

set -euo pipefail

PROJECT_ID="${1:?usage: bootstrap.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
STATE_BUCKET="${PROJECT_ID}-tfstate"

echo "==> Project: ${PROJECT_ID} (${REGION})"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "==> Enabling bootstrap APIs"
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  serviceusage.googleapis.com \
  iam.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}"

if gcloud storage buckets describe "gs://${STATE_BUCKET}" >/dev/null 2>&1; then
  echo "==> State bucket gs://${STATE_BUCKET} already exists"
else
  echo "==> Creating state bucket gs://${STATE_BUCKET}"
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention

  # Versioning is not optional for Terraform state — it is the only recovery
  # path from a corrupted or truncated state write.
  gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
fi

cat <<EOF

==> Bootstrap complete.

Next:
  cd terraform
  terraform init -backend-config="bucket=${STATE_BUCKET}"
  cp terraform.tfvars.example terraform.tfvars   # then edit it
  terraform apply -var="project_id=${PROJECT_ID}"

EOF
