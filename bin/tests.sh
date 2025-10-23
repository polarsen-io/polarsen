#!/usr/bin/env bash

# DESCRIPTION: Run the test suite
# USAGE: ./bin/tests.sh [<args>]
# EXAMPLES:
#   - ./bin/tests.sh
#   - ./bin/tests.sh --no-run

set -eou pipefail

cd "$(dirname "$0")/.." || exit 1

export DB_NAME='polarsen_test'

_no_run=false
if [ "${1:-}" == "--no-run" ]; then
  _no_run=true
  shift
fi

# Check if .env exists otherwise copy from template.env
if [[ ! -f "infra/.env" ]]; then
  echo "No .env file found. Copying from template.env"
  cp infra/template.env infra/.env
fi

./bin/start.sh up -d db s3
./bin/start.sh --profile test run pg-init-test
if [ "$_no_run" = false ]; then
  ./bin/start.sh --profile test run --rm test uv run pytest "${@}"
fi




