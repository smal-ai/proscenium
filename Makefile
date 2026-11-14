.PHONY: bootstrap generate check-generated spec-check lint typecheck build test-unit test-contracts test-integration-offline test-browser test-native test-browser-installed

PYTHON_CHECK_PATHS = apps/backend/src tests scripts $(wildcard agent/skills tools/source tools/blender tools/comfy tools/unity tools/image_to_3d tools/operations)
CONFIG ?= config.example.json

.PHONY: run
run: build
	uv run python -m proscenium --config "$(CONFIG)" $(ARGS)

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
	uv run ruff check $(PYTHON_CHECK_PATHS)
	uv run ruff format --check $(PYTHON_CHECK_PATHS)
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
