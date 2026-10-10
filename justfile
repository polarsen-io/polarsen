set quiet

# List available recipes
default:
    @just --list

# Bootstrap: install deps + init db & s3
[confirm('This will reinitialize the database. Continue?')]
[group('setup')]
bootstrap:
    uv sync
    ./bin/start.sh up -d db s3
    ./bin/start.sh run --rm pg-init
    ./bin/start.sh run --rm s3-init
    ./bin/tests.sh --no-run

# Run tests
[group('dev')]
test *args:
    ./bin/tests.sh {{ args }}

# Lint
[group('dev')]
lint:
    uv run ruff check .

# Format
[group('dev')]
fmt:
    uv run ruff format .

# Type check
[group('dev')]
check:
    uv run pyright

# Lint + type check + test
[group('dev')]
ci: lint check test

# Start db and s3
[group('infra')]
infra:
    ./bin/start.sh up -d db s3

# Stop all services
[group('infra')]
down:
    ./bin/start.sh down

# Start api + bot
[group('app')]
app: infra
    ./bin/start.sh up --build api bot

# Start api + bot in watch mode
[group('app')]
app-watch: infra
    ./bin/start.sh up --build --watch api bot

# Start all listeners
[group('listeners')]
listeners: infra
    COMPOSE_PROFILES=listener ./bin/start.sh up --build

# Start all listeners in watch mode
[group('listeners')]
listeners-watch: infra
    COMPOSE_PROFILES=listener ./bin/start.sh up --build --watch

# Start everything (default: watch mode)
[group('all')]
up:
    ./bin/start.sh

# Start everything without watch
[group('all')]
up-no-watch:
    COMPOSE_PROFILES=listener ./bin/start.sh up --build

# Run CLI commands (sources .env)
[group('dev')]
cli *args:
    ./bin/cli.sh {{ args }}

# Generate API models from OpenAPI spec
[group('dev')]
gen-models *args:
    ./bin/gen-api-model.sh {{ args }}