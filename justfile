# List available recipes
help:
    @just --list

# Install dependencies [dev]
install:
    uv sync --locked --all-groups

# Format code [ruff]
format:
    uvx ruff format

# Run linters [ruff]
lint:
    uvx ruff check

# Run pre-commit [lint, format]
pre-commit: lint format
    uvx prek run

# Typecheck using ty
typecheck:
    uvx ty check

# Run using Pytest
test:
    uv run pytest -q

# Run the bot locally on port 8000
run:
    uv run python -m bot_wumtaaba.server --host 127.0.0.1 --port 8000

# Clean cache files
clean:
    find . -type d -name __pycache__ -exec rm -rf {} +
    rm -rf .pytest_cache .ruff_cache
