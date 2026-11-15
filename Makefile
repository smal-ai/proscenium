.PHONY: bootstrap run build generate check-generated lint typecheck test test-browser check

CONFIG ?= config.example.json
PYTHON_CHECK_PATHS = apps/backend/src scripts tests tools agent/skills

bootstrap:
	uv sync --frozen --python 3.12
	npm ci --ignore-scripts

run: build
	uv run python -m proscenium --config "$(CONFIG)" $(ARGS)

build:
	npm run build

generate:
	uv run python scripts/generate_contracts.py
	npm run generate

check-generated:
	uv run python scripts/generate_contracts.py --check
	uv run python scripts/check_generated.py

lint:
	uv run ruff check $(PYTHON_CHECK_PATHS)
	uv run ruff format --check $(PYTHON_CHECK_PATHS)
	npm run lint

typecheck:
	uv run mypy apps/backend/src
	npm run typecheck

test: build
	npm run test:unit
	uv run pytest -q tests

test-browser:
	npx playwright test --config playwright.config.ts --project=chromium-*

check: lint typecheck check-generated test
