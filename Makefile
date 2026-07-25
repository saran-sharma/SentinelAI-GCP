.DEFAULT_GOAL := help
SHELL := /bin/bash

PROJECT_ID ?= $(shell gcloud config get-value project 2>/dev/null)
REGION     ?= us-central1
TF         := terraform -chdir=terraform

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- local development ------------------------------------------------------

.PHONY: install
install: ## Install dev dependencies
	pip install -r requirements-dev.txt

.PHONY: test
test: ## Run the test suite with coverage
	pytest --cov=app --cov-report=term-missing

.PHONY: lint
lint: ## Lint and format-check
	ruff check .
	ruff format --check .

.PHONY: fmt
fmt: ## Auto-format Python and Terraform
	ruff check --fix .
	ruff format .
	$(TF) fmt -recursive

.PHONY: run
run: ## Run the API locally (no GCP calls until a request arrives)
	SENTINEL_PROJECT_ID=$(PROJECT_ID) \
	SENTINEL_VERIFY_OIDC=false \
	uvicorn app.main:app --reload --port 8080

# --- infrastructure ---------------------------------------------------------

.PHONY: bootstrap
bootstrap: ## Create the Terraform state bucket and enable base APIs
	./scripts/bootstrap.sh $(PROJECT_ID) $(REGION)

.PHONY: init
init: ## terraform init against the remote backend
	$(TF) init -backend-config="bucket=$(PROJECT_ID)-tfstate"

.PHONY: plan
plan: ## terraform plan
	$(TF) plan -var="project_id=$(PROJECT_ID)" -var="region=$(REGION)"

.PHONY: apply
apply: ## terraform apply
	$(TF) apply -var="project_id=$(PROJECT_ID)" -var="region=$(REGION)"

.PHONY: destroy
destroy: ## Tear everything down
	$(TF) destroy -var="project_id=$(PROJECT_ID)" -var="region=$(REGION)"

.PHONY: validate
validate: ## Validate Terraform without credentials
	$(TF) fmt -check -recursive
	$(TF) init -backend=false -input=false
	$(TF) validate

# --- build & deploy ---------------------------------------------------------

IMAGE := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/sentinelai/sentinelai-triage

.PHONY: build
build: ## Build the container image
	docker build -t $(IMAGE):$(shell git rev-parse --short HEAD) .

.PHONY: push
push: build ## Push the image to Artifact Registry
	gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet
	docker push $(IMAGE):$(shell git rev-parse --short HEAD)

.PHONY: deploy
deploy: push ## Build, push and apply with the new image
	$(TF) apply -auto-approve \
		-var="project_id=$(PROJECT_ID)" \
		-var="region=$(REGION)" \
		-var="container_image=$(IMAGE):$(shell git rev-parse --short HEAD)"

# --- verification -----------------------------------------------------------

.PHONY: smoke
smoke: ## Run post-deploy smoke tests
	./scripts/smoke_test.sh $(PROJECT_ID) $(REGION)

.PHONY: demo
demo: ## Publish a realistic incident burst and show the triage result
	./scripts/simulate_incident.sh $(PROJECT_ID)

.PHONY: digest
digest: ## Trigger the reliability digest now
	gcloud scheduler jobs run sentinelai-daily-digest --location=$(REGION) --project=$(PROJECT_ID)

.PHONY: logs
logs: ## Tail structured triage logs
	gcloud logging read \
		'resource.type="cloud_run_revision" AND resource.labels.service_name="sentinelai-triage"' \
		--project=$(PROJECT_ID) --limit=50 --format=json --freshness=1h
