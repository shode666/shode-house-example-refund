.PHONY: up down test test-local install

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

up:
	docker compose up -d db_test
	docker compose exec -T db_test sh -c 'until pg_isready -U postgres; do sleep 1; done'

down:
	docker compose down -v

# For humans on macOS with docker compose available.
test: up
	DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/refund_test" \
		.venv/bin/pytest -v
	$(MAKE) down

# For environments without docker: point at an already-running throwaway
# PostgreSQL cluster (see README.md "Running tests without docker").
test-local:
	.venv/bin/pytest -v
