#!/usr/bin/env bash

# DESCRIPTION: Run the test suite
# USAGE: ./bin/tests.sh [<args>]
# EXAMPLES:
#   - ./bin/tests.sh                    # Run all tests in Docker

set -eou pipefail

cd "$(dirname "$0")/.." || exit 1

export DB_NAME='polarsen_test'

# Add a --no-run flag to only setup the bucket without running tests

_no_run=false
if [ "${1:-}" == "--no-run" ]; then
  _no_run=true
  shift
fi

#setup_minio(){
##  docker exec infra-s3-1 sh -c "mc alias set local http://localhost:9000 minioadmin minioadmin && mc mb --ignore-existing local/$S3_BUCKET"
#}

# Start the infrastructure services if not running
./bin/start.sh up -d db s3 pg-init-test
#./bin/setup-pg.sh --drop --quiet
#./bin/start.sh --profile test

if [ "$_no_run" = false ]; then
  ./bin/start.sh --profile test run --build --rm test uv run pytest "${@}"
else
  ./bin/start.sh logs db s3 -f
fi
