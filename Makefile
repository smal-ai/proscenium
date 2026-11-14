.PHONY: bootstrap generate check-generated spec-check lint typecheck build test-unit test-contracts test-integration-offline test-browser test-native test-browser-installed

bootstrap:
	uv sync --frozen --python 3.12
	npm ci --ignore-scripts

generate:
	uv run python scripts/generate_contracts.py
	npm run generate

check-generated:
	uv run python scripts/generate_contracts.py --check
	uv run python scripts/check_generated.py

spec-check:
	uv run python docs/validate_spec.py
	$(MAKE) check-generated

lint:
	uv run ruff check apps/backend/src tests scripts
	uv run ruff format --check apps/backend/src tests scripts
	npm run lint

typecheck:
	uv run mypy apps/backend/src
	npm run typecheck
	npx tsc --project tsconfig.tests.json

build:
	npm run build

test-unit:
	uv run pytest -q tests/unit

test-contracts:
	uv run pytest -q tests/contracts

test-integration-offline:
	uv run pytest -q tests/integration -m "not native" --ignore=tests/integration/native

test-browser:
	uv run python scripts/run_suite.py browser

test-native:
	uv run python scripts/run_suite.py native --selector "$(PROFILE)"

test-browser-installed:
	uv run python scripts/run_suite.py browser-installed --selector "$(BROWSER)"
