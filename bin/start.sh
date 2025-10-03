#!/usr/bin/env bash

# DESCRIPTION: Start the Polarsen services
#   By default, it runs in watch mode.
# USAGE: ./bin/start.sh [<args>]
# EXAMPLE:
#   - ./bin/start.sh
#   - ./bin/start.sh up -d
#   - ./bin/start.sh build


set -eou pipefail

cd "$(dirname "$0")/.." || exit 1

# If no args are provided, we start in watch mode
if [ $# -eq 0 ]; then
  _args="up --build -w"
else
  _args="$@"
fi

docker compose -f infra/compose.yml $_args
