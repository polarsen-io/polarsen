#!/usr/bin/env bash

# DESCRIPTION: Generate API model files from OpenAPI specification.
# USAGE: ./gen-api-model.sh <openapi-spec-file> <output-dir>

set -eou pipefail

cd "$(dirname "$0")/.." || exit 1

readonly _url=${1:-http://localhost:5050/openapi.json}

echo "Generating API model files from OpenAPI specification..."
uv run datamodel-codegen --url "$_url" \
  --input-file-type openapi \
  --output ./polarsen/bot/models.py \
  --use-standard-collections \
  --enum-field-as-literal all \
  --target-python-version "$(cat .python-version)" \
  --output-model-type typing.TypedDict
echo "API model files generated successfully in ./polarsen/bot/models.py"