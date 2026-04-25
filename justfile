# Course Scout Project Automation
set shell := ["sh", "-cu"]

# --- Project Constants ---
PY         := "uv run python"
PYTEST     := "uv run pytest"
RUFF       := "uv run ruff"
SRC        := "src"
TESTS      := "tests"
IMAGE      := "course-scout-course-scout:latest"
COMPOSE    := "docker compose -f docker-compose.local.yml"

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
    uv run course-scout-worker

# Start SSE server
@sse:
    uv run course-scout-sse

# Run scan locally (yesterday, all topics)
@scan *args:
    uv run course-scout scan {{args}}

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
    {{PYTEST}} --cov=src/course_scout --cov-report=term-missing

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

# Run full project QA (Tests + Linting + Formatting + Types + Architecture)
@qa:
    @echo "--- 🤖 Course Scout QA ---"
    just fix
    just check-types
    just check-architecture
    just coverage
    @echo "✅ All code QA checks passed!"

# --- Docker QA ---

# Build the image via compose
@docker-build:
    {{COMPOSE}} build

# Smoke test: container launches and CLI resolves
@docker-smoke:
    @echo "--- 🐳 Docker smoke test ---"
    docker run --rm {{IMAGE}} course-scout --help > /dev/null
    @echo "✅ CLI entrypoint resolves inside container"

# Config test: compose file validates
@docker-config:
    {{COMPOSE}} config --quiet
    @echo "✅ docker-compose.local.yml validates"

# Dry-run scan inside container (today, no PDF, throwaway reports dir)
@docker-scan-dry:
    @echo "--- 🐳 Container dry-run scan (today, no PDF) ---"
    mkdir -p /tmp/cs-dry-reports
    docker run --rm \
        -v /tmp/cs-dry-reports:/app/reports \
        -v $HOME/.claude.json:/home/appuser/.claude.json:ro \
        -v $HOME/.claude:/home/appuser/.claude:ro \
        -v $PWD/course_scout.session:/app/course_scout.session \
        -v $PWD/.env:/app/.env:ro \
        {{IMAGE}} \
        course-scout scan --today --no-pdf
    @ls -la /tmp/cs-dry-reports/ || echo "No reports produced"

# Full docker QA chain
@docker-qa:
    just docker-build
    just docker-config
    just docker-smoke
    @echo "✅ All Docker QA checks passed!"

# --- Cron QA ---

# Inspect the crontab inside the scheduler container
@cron-inspect:
    @echo "--- 📅 Scheduler crontab ---"
    @docker exec course-scout-cron crontab -l 2>&1 || echo "Scheduler not running (docker compose up -d scheduler)"

# Manually fire the scan exactly as cron would
@cron-dry-fire:
    @echo "--- 📅 Manual cron fire (same as 1am trigger) ---"
    docker start -a course-scout

# --- Deploy ---

# Start the scheduler (1am daily cron)
@cron-start:
    {{COMPOSE}} up -d scheduler
    @echo "✅ Scheduler running. Cron: 0 1 * * * docker start -a course-scout"
    @just cron-inspect

# Stop the scheduler
@cron-stop:
    {{COMPOSE}} stop scheduler
    @echo "✅ Scheduler stopped."

# --- Cleanup ---

# Clean up build artifacts and caches
@clean:
    @echo "🧹 Cleaning project..."
    rm -rf dist/
    find . -type d -name "__pycache__" -exec rm -rf {} +
    rm -rf .pytest_cache/ .ruff_cache/ .import_linter_cache/
    @echo "✨ Cleaned."
