.PHONY: help venv install lint format typecheck test test-cov clean

help: ## Show this help message
	@echo 'Usage: make <target>'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create virtual environment
	@echo "🐍 Creating virtual environment..."
	@python3 -m venv .venv
	@echo "✅ Virtual environment created"

install: venv ## Install all dependencies (including dev)
	@echo "📦 Installing dependencies..."
	@.venv/bin/pip install --upgrade pip
	@.venv/bin/pip install -e ".[dev]"
	@echo "✅ Dependencies installed"

lint: ## Run linting and formatting check
	@echo "🔍 Running linter..."
	@.venv/bin/ruff check src/ tests/
	@echo "✅ Linting passed"
	@echo "📝 Running formatter check..."
	@.venv/bin/ruff format --check src/ tests/
	@echo "✅ Formatting passed"

format: ## Run code formatting
	@echo "📝 Running code formatter..."
	@.venv/bin/ruff check --fix src/ tests/
	@.venv/bin/ruff format src/ tests/
	@echo "✅ Code formatted"

typecheck: ## Run type checking
	@echo "🔎 Running type checker..."
	@.venv/bin/mypy src/
	@echo "✅ Type checking passed"

test: ## Run all tests with per-file coverage gate
	@echo "🧪 Running tests..."
	@.venv/bin/pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=xml --cov-fail-under=90 --junit-xml=junit.xml
	@echo "🔒 Enforcing per-file coverage gate (each component >= 90%)..."
	@.venv/bin/python scripts/check-per-file-coverage.py
	@echo "🚫 Enforcing zero-skip policy..."
	@.venv/bin/python scripts/check-no-skipped-tests.py
	@echo "✅ Tests passed"

test-cov: ## Run tests with HTML coverage report + per-file gate
	@echo "🧪 Running tests with coverage..."
	@.venv/bin/pytest tests/ -v --cov=src --cov-report=html --cov-report=term-missing --cov-report=xml --cov-fail-under=90
	@echo "🔒 Enforcing per-file coverage gate (each component >= 90%)..."
	@.venv/bin/python scripts/check-per-file-coverage.py
	@echo "✅ Tests passed"
	@echo "📊 Open htmlcov/index.html to view coverage report"

clean: ## Remove build + test artefacts
	rm -rf build/ dist/ *.egg-info/ .pytest_cache/ .coverage coverage.xml htmlcov/ junit.xml .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
