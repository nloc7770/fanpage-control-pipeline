# Shortform Factory -- dev shortcuts
# Usage: make <target>

SHELL := /bin/bash
COMPOSE := docker compose
COMPOSE_GPU := docker compose -f docker-compose.yml -f docker-compose.gpu.yml

.PHONY: help bootstrap up up-gpu down restart logs ps build \
        migrate makemigration downgrade seed shell-api shell-db psql redis-cli \
        lint typecheck test fmt clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | sort | \
	    awk 'BEGIN {FS = ":.*?##"}; {printf "  %-18s %s\n", $$1, $$2}'

bootstrap: ## one-shot dev setup (python venv + pnpm install)
	@bash scripts/bootstrap.sh

up: ## start postgres, redis, api, worker-cpu, frontend
	$(COMPOSE) up -d postgres redis api worker-cpu frontend

up-gpu: ## start everything including GPU workers
	$(COMPOSE_GPU) up -d

down: ## stop and remove containers
	$(COMPOSE) down

restart: ## restart api + workers
	$(COMPOSE) restart api worker-cpu

logs: ## tail logs from all services
	$(COMPOSE) logs -f --tail=200

ps: ## list running services
	$(COMPOSE) ps

build: ## rebuild images
	$(COMPOSE) build

migrate: ## alembic upgrade head
	$(COMPOSE) exec api alembic -c packages/database/alembic.ini upgrade head

makemigration: ## alembic revision --autogenerate -m "$(MSG)"
	$(COMPOSE) exec api alembic -c packages/database/alembic.ini revision --autogenerate -m "$(MSG)"

downgrade: ## alembic downgrade -1
	$(COMPOSE) exec api alembic -c packages/database/alembic.ini downgrade -1

seed: ## insert a demo job
	$(COMPOSE) exec api python scripts/seed.py

shell-api: ## bash into the api container
	$(COMPOSE) exec api bash

shell-db: ## psql into postgres
	$(COMPOSE) exec postgres psql -U factory -d factory

psql: shell-db

redis-cli: ## redis-cli into redis
	$(COMPOSE) exec redis redis-cli

lint:
	$(COMPOSE) exec api ruff check apps packages
	cd apps/frontend && pnpm lint

typecheck:
	$(COMPOSE) exec api mypy apps packages
	cd apps/frontend && pnpm typecheck

test:
	$(COMPOSE) exec api pytest -q
	cd apps/frontend && pnpm test

fmt:
	$(COMPOSE) exec api ruff format apps packages
	cd apps/frontend && pnpm fmt

clean:
	$(COMPOSE) down -v
	rm -rf node_modules apps/*/node_modules packages/*/node_modules
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
