PYTHON := $(shell command -v python3.12 2>/dev/null || command -v python3 2>/dev/null)

.PHONY: up down test test-local install

# File target (not .PHONY): make only rebuilds the venv when
# .venv/bin/pytest is missing, so `make test` / `make test-local` work
# standalone on a clean checkout without a separate `make install` step
# (root cause of the macOS failure: install was a disconnected target).
.venv/bin/pytest:
	@if [ -z "$(PYTHON)" ]; then \
		echo "ERROR: no python3 found on PATH. Install Python >=3.12 and retry."; exit 1; \
	fi
	@ver=$$($(PYTHON) -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'); \
	major=$$(echo $$ver | cut -d. -f1); minor=$$(echo $$ver | cut -d. -f2); \
	if [ "$$major" -lt 3 ] || { [ "$$major" -eq 3 ] && [ "$$minor" -lt 12 ]; }; then \
		echo "ERROR: need Python >=3.12, found $(PYTHON) (version $$ver). Install python3.12 and retry."; \
		exit 1; \
	fi
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"

install: .venv/bin/pytest

up:
	docker compose up -d db_test
	docker compose exec -T db_test sh -c 'until pg_isready -U postgres; do sleep 1; done'

down:
	docker compose down -v

# The one command for humans on macOS with docker compose available.
test: .venv/bin/pytest up
	DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5433/refund_test" \
		.venv/bin/pytest -v
	$(MAKE) down

# For environments without docker: point at an already-running throwaway
# PostgreSQL cluster (see README.md "Running tests without docker").
test-local: .venv/bin/pytest
	.venv/bin/pytest -v
