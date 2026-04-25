# Telebot Project Automation
set shell := ["sh", "-cu"]
 
# --- Project Constants ---
PY         := "uv run python"
PYTEST     := "uv run pytest"
RUFF       := "uv run ruff"
SRC        := "src"
TESTS      := "tests"
 
# Default: List all available tasks
default:
    @just --list
 
# --- Setup ---
 
# Install dependencies
@install:
    uv sync
    uv run pre-commit install || true
 
# --- Development ---
 
# Start background worker
@worker:
    uv run telebot-worker
 
# Start SSE server
@sse:
    uv run telebot-sse

# Publish a TaskNotes Inbox stub for the most recent scan (or pass --date YYYY-MM-DD).
# Run this on the Mac (vault is OneDrive-synced from a Mac path); won't work
# inside the NAS Docker container.
@post-task *args:
    uv run course-scout post-task {{args}}

# --- QA & CI ---
 
# Run tests
test *args:
    {{PYTEST}} {{args}}
 
# Run tests with coverage
coverage:
    {{PYTEST}} --cov=src/telebot --cov-report=term-missing
 
# Lint code with Ruff
@lint:
    {{RUFF}} check {{SRC}} {{TESTS}} scripts/
 
# Format code with Ruff
@format:
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/
 
# Fix all auto-fixable issues
@fix:
    {{RUFF}} check --fix {{SRC}} {{TESTS}} scripts/
    {{RUFF}} format {{SRC}} {{TESTS}} scripts/
 
# Static type checking
@check-types:
    uv run pyright {{SRC}}
 
# Check architectural layers and isolation
@check-architecture:
    uv run lint-imports
 
# Run full project QA (Tests + Linting + Formatting)
@qa:
    @echo "--- 🤖 Telebot QA ---"
    just fix
    just check-types
    just check-architecture
    just coverage
    @echo "✅ All QA checks passed!"
 
# --- Cleanup ---
 
# Clean up build artifacts and caches
@clean:
    @echo "🧹 Cleaning project..."
    rm -rf dist/
    find . -type d -name "__pycache__" -exec rm -rf {} +
    rm -rf .pytest_cache/ .ruff_cache/ .import_linter_cache/
    @echo "✨ Cleaned."
