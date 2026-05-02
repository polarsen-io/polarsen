# syntax=docker/dockerfile:1.4
# check=error=true
FROM ghcr.io/astral-sh/uv:python3.13-alpine AS builder

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

ARG UV_PARAMS="--only-group default --only-group llms --only-group cli"

RUN apk add --no-cache \
    build-base \
    cmake \
    pkgconfig \
    linux-headers

# Install dependencies to system Python
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-${PROJECT_MODE} \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev ${UV_PARAMS}

# Copy project files
ADD pyproject.toml uv.lock ./

# Install the project to system Python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev ${UV_PARAMS}

FROM --platform=linux/amd64 rg.fr-par.scw.cloud/polarsen/psql:18-alpine AS pg-amd64
FROM alpine:3 AS pg-arm64
FROM pg-${TARGETARCH:-amd64} AS pg

FROM python:3.13-alpine AS main

ARG version
ENV VERSION=$version
ARG PROJECT_MODE
ENV PROJECT_MODE=$PROJECT_MODE
ARG TARGETARCH

RUN addgroup user && \
    adduser -s /bin/bash -D -G user user

# Install runtime dependencies
# On arm64 (Apple Silicon), postgresql18-client from apk is used since the custom pg image is amd64-only
RUN apk add --no-cache \
    libedit \
    krb5-libs \
    openldap \
    postgresql18-client

COPY --from=builder /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
# On amd64, override with binaries from the custom pg image
RUN --mount=type=bind,from=pg,source=/,target=/pg \
    if [ "$TARGETARCH" = "amd64" ] && [ -f /pg/usr/local/bin/psql ]; then \
      cp /pg/usr/local/bin/psql /usr/local/bin/psql && \
      cp /pg/usr/local/bin/dropdb /usr/local/bin/dropdb && \
      cp /pg/usr/local/bin/createdb /usr/local/bin/createdb && \
      cp /pg/usr/local/lib/libpq.* /usr/local/lib/; \
    fi

ADD polarsen/ polarsen/
ADD sql/ sql/

USER user

ENTRYPOINT ["python", "-m", "polarsen"]

