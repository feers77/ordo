.PHONY: up down health migrate seed check lint types test test-load test-agent schema new-module new-loc

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

test-load:
	@echo "pendiente: k6 (F2)" && exit 1

test-agent: ## Suite agéntica
	uv run pytest tests/agent -m agent

schema:
	@echo "pendiente: generación OpenAPI + schema semántico (F2)" && exit 1

new-module:
	@echo "pendiente: scaffolding de módulo (F2)" && exit 1

new-loc:
	@echo "pendiente: scaffolding de localización (F7)" && exit 1
