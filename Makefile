.DEFAULT_GOAL := help

.PHONY: help setup dev api web _api _web test lint typecheck build evaluate live-evaluate

API_PORT ?= 8000
WEB_PORT ?= 3000
AVAILABLE_API_PORT = $(shell uv run python scripts/find_available_port.py $(API_PORT))
AVAILABLE_WEB_PORT = $(shell uv run python scripts/find_available_port.py $(WEB_PORT))

help: ## Show available commands
	@echo CloudCause development commands:
	@echo   make setup      Install Python and web dependencies
	@echo   make dev        Start the API and web app together
	@echo   make api        Start only the API
	@echo   make web        Start only the web app
	@echo   make test       Run the offline test suite
	@echo   make lint       Run Python lint checks
	@echo   make typecheck  Check web TypeScript types
	@echo   make build      Build the web application
	@echo   make evaluate   Run the offline evaluation harness
	@echo   make live-evaluate  Run paid hosted-model evaluation, requires API keys

setup: ## Install Python and web dependencies
	uv sync
	npm --prefix web install

dev: ## Start the API and web app together; uses different ports when defaults are occupied
	$(MAKE) --no-print-directory -j2 _api _web API_PORT=$(AVAILABLE_API_PORT) WEB_PORT=$(AVAILABLE_WEB_PORT)

api: ## Start the API; uses the next available port starting at API_PORT (default 8000)
	$(MAKE) --no-print-directory _api API_PORT=$(AVAILABLE_API_PORT)

web: ## Start the web app; uses the next available port starting at WEB_PORT (default 3000)
	$(MAKE) --no-print-directory _web WEB_PORT=$(AVAILABLE_WEB_PORT)

_api: export CLOUDCAUSE_API_PORT = $(API_PORT)
_api:
	@echo Starting CloudCause API at http://127.0.0.1:$(API_PORT)/docs
	uv run cloudcause-api

_web: export CLOUDCAUSE_API_URL = http://127.0.0.1:$(API_PORT)
_web: export CLOUDCAUSE_WEB_DIST_DIR = .next-dev
_web:
	@echo Starting CloudCause web app at http://127.0.0.1:$(WEB_PORT) with API http://127.0.0.1:$(API_PORT)
	npm --prefix web run dev -- --hostname 127.0.0.1 --port $(WEB_PORT)

test: ## Run the offline test suite
	uv run pytest tests -q

lint: ## Run Python lint checks
	uv run ruff check .

typecheck: ## Check web TypeScript types
	npm --prefix web run typecheck

build: ## Build the web application
	npm --prefix web run build

evaluate: ## Run the offline evaluation harness
	uv run python evaluations/run_evaluation.py

live-evaluate: ## Run real framework agents against fixture evidence (requires keys)
	uv run python scripts/require_live_keys.py
	uv run pytest tests/live -m live -q
