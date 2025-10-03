#!/usr/bin/env bash

# DESCRIPTION: Initialize S3 bucket
# USAGE: ./init-s3.sh
# EXAMPLE:
#   - ./init-s3.sh

set -eou pipefail

# Wait for MinIO to be ready
until mc alias set local "http://s3:9000" ${MINIO_ROOT_USER:-minioadmin} ${MINIO_ROOT_PASSWORD:-minioadmin}; do
  echo "Waiting for MinIO to be ready..."
  sleep 1
done

# Parse BUCKETS=foo,bar and create each bucket
IFS=',' read -ra BUCKETS <<< "${BUCKETS}"
for BUCKET in "${BUCKETS[@]}"; do
  echo "Creating bucket local/${BUCKET}"
  mc mb "local/${BUCKET}" --ignore-existing
done
