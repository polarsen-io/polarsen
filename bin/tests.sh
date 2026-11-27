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

# Check if .env exists otherwise generate one from template.env
_random_hex() {
  head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
}

if [[ ! -f "infra/.env" ]]; then
  echo "No .env file found. Generating infra/.env from template.env"
  _pg_password="$(_random_hex 16)"
  _s3_access_key="$(_random_hex 12)"
  _s3_secret_key="$(_random_hex 24)"
  sed \
    -e "s|^PG_PASSWORD=.*|PG_PASSWORD=${_pg_password}|" \
    -e "s|^S3_ACCESS_KEY_ID=.*|S3_ACCESS_KEY_ID=${_s3_access_key}|" \
    -e "s|^S3_SECRET_ACCESS_KEY=.*|S3_SECRET_ACCESS_KEY=${_s3_secret_key}|" \
    infra/template.env > "infra/.env"
fi

./bin/start.sh up -d db s3
./bin/start.sh --profile test run pg-init-test
if [ "$_no_run" = false ]; then
  ./bin/start.sh --profile test run --rm test uv run pytest "${@}"
fi




