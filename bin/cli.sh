#!/usr/bin/env bash

# DESCRIPTION: Run the main script
# USAGE: ./bin/cli.sh <args>

set -e

cd "$(dirname "$0")/.." || exit 1

set -a
source .env
set +a

uv run -m polarsen "${@}"