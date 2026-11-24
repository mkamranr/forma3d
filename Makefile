# Forma3D developer commands. Works against the active `docker context`
# (e.g. `docker context use forma3d-remote` for the remote Docker host).

COMPOSE      = docker compose --env-file .env -f deployment/docker-compose.yml
COMPOSE_DEV  = $(COMPOSE) -f deployment/docker-compose.dev.yml

.PHONY: help env up up-dev down logs ps migrate test test-backend test-frontend lint build-frontend

help:
	@echo "env            create .env from .env.example"
	@echo "up / up-dev    build and start the stack (prod-like / dev overrides)"
	@echo "down           stop the stack"
	@echo "logs / ps      follow logs / show service status"
	@echo "migrate        run alembic upgrade head"
	@echo "test           backend tests in container + frontend tests locally"

env:
	@test -f .env || cp .env.example .env && echo ".env ready (edit secrets!)"

up:
	$(COMPOSE) up -d --build --wait

up-dev:
	$(COMPOSE_DEV) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

migrate:
	$(COMPOSE) run --rm migrate

test: test-backend test-frontend

test-backend:
	$(COMPOSE_DEV) run --rm --no-deps backend pytest -q

test-frontend:
	cd frontend && npm test

lint:
	cd backend && ruff check app tests
	cd frontend && npm run typecheck

build-frontend:
	cd frontend && npm run build
