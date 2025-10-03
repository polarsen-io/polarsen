#!/usr/bin/env bash

# DESCRIPTION: Generate API model files from OpenAPI specification.
# USAGE: ./gen-api-model.sh <openapi-spec-file> <output-dir>

cd "$(dirname "$0")/.." || exit 1


echo "Generating API model files from OpenAPI specification..."
uv run datamodel-codegen --url $1 \
  --input-file-type openapi \
  --output ./polarsen/bot/models.py \
  --use-standard-collections \
  --enum-field-as-literal all \
  --target-python-version $(cat .python-version) \
  --output-model-type typing.TypedDict
echo "API model files generated successfully in ./polarsen/bot/models.py"