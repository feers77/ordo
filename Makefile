.PHONY: up down health migrate seed check lint types test test-load test-agent schema new-module new-loc docs-serve

COMPOSE := docker compose --env-file infra/compose/.env -f infra/compose/docker-compose.yml

up: ## Levanta stack local (core + dev)
	$(COMPOSE) --profile dev up -d --wait

up-obs: ## Observabilidad (requiere RAM adicional)
	$(COMPOSE) --profile obs up -d

down:
	$(COMPOSE) --profile dev --profile obs down

health: ## Estado de healthchecks del stack
	@$(COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'

migrate:
	@echo "pendiente: migraciones Alembic (F2)" && exit 1

seed:
	@echo "pendiente: datos demo (F2)" && exit 1

lint:
	uv run ruff check .
	uv run ruff format --check .

types:
	uv run mypy

test:
	uv run pytest tests/unit

check: lint types test ## lint + types + tests

test-load: ## SLO de §4.3 con k6 (requiere el stack y ordo-api corriendo)
	k6 run -e ORDO_URL=$(or $(ORDO_URL),http://127.0.0.1:8000) \
	       -e ORDO_TENANT=$(or $(ORDO_TENANT),loadtest) tests/load/slo.js

test-e2e: ## E2E contra el stack real
	uv run pytest tests/e2e -m e2e

test-agent: ## Suite agéntica
	uv run pytest tests/agent -m agent

docs-serve: ## Documentación de la API en http://localhost:8888
	uv run python docs/landing/serve.py

schema:
	@echo "pendiente: generación OpenAPI + schema semántico (F2)" && exit 1

new-module: ## Crea el esqueleto de un módulo: make new-module NAME=ventas
	uv run python tools/new_module.py $(NAME) --depends "$(DEPENDS)"

new-loc:
	@echo "pendiente: scaffolding de localización (F7)" && exit 1
