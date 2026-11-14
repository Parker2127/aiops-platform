SHELL := /bin/bash
DEV   := infra/envs/dev

.PHONY: help install lint fmt test tf-check manifests check train train-sagemaker images argocd loadtest teardown

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

install: ## Create the dev venv (uv)
	uv sync

lint: ## ruff lint + format check
	uv run ruff check ml apps
	uv run ruff format --check ml apps

fmt: ## Auto-format Python + Terraform
	uv run ruff check --fix ml apps
	uv run ruff format ml apps
	terraform fmt -recursive infra

test: ## All unit tests (no AWS needed)
	uv run pytest

tf-check: ## terraform fmt + validate (no backend)
	terraform fmt -check -recursive infra
	terraform -chdir=infra/bootstrap init -backend=false -input=false >/dev/null && terraform -chdir=infra/bootstrap validate
	terraform -chdir=$(DEV) init -backend=false -input=false >/dev/null && terraform -chdir=$(DEV) validate

manifests: ## kustomize-build every app
	@for d in apps/*/k8s; do kubectl kustomize $$d >/dev/null && echo "$$d ok"; done

check: lint test tf-check manifests ## Everything CI runs
	shellcheck scripts/*.sh

train: ## Generate data + train locally
	uv run python ml/generate_logs.py
	uv run python ml/train.py

train-sagemaker: ## Train on SageMaker and pin the model URI (bills AWS)
	uv run python ml/train_sagemaker.py --upload-data --wait --pin

images: ## Build + push all images, pin tags (bills AWS: ECR)
	./scripts/build-push.sh

argocd: ## Install Argo CD + app-of-apps
	./scripts/deploy-argocd.sh

loadtest: ## Ramp load on the demo app
	./scripts/loadtest.sh

teardown: ## Destroy everything that bills
	./scripts/teardown.sh
