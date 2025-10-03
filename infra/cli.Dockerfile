# syntax=docker/dockerfile:1.4
# check=error=true
FROM andarius/python:3.13-alpine AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

ARG UV_PARAMS="--only-group llms --only-group default --only-group cli"

RUN apk add --no-cache \
    build-base \
    cmake \
    pkgconfig \
    linux-headers

# Install dependencies to system Python
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev ${UV_PARAMS}

# Copy project files
ADD pyproject.toml uv.lock ./

# Install the project to system Python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev ${UV_PARAMS}

FROM rg.fr-par.scw.cloud/polarsen/psql:18-alpine as pg
FROM python:3.13-alpine AS main

ARG version
ENV VERSION=$version
ARG PROJECT_MODE
ENV PROJECT_MODE=$PROJECT_MODE

RUN addgroup user && \
    adduser -s /bin/bash -D -G user user

# Install runtime dependencies that psql needs
RUN apk add --no-cache \
    libedit \
    krb5-libs \
    openldap

COPY --from=builder /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
# PG client
COPY --from=pg /usr/local/bin/psql /usr/local/bin/psql
COPY --from=pg /usr/local/bin/dropdb /usr/local/bin/dropdb
COPY --from=pg /usr/local/bin/createdb /usr/local/bin/createdb
COPY --from=pg /usr/local/lib/libpq.* /usr/local/lib/

ADD polarsen/ polarsen/
ADD sql/ sql/

USER user

ENTRYPOINT ["python", "-m", "polarsen"]

