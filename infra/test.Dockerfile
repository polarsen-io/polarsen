# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
# check=error=true
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:63b8fa4be2338728034164eb6c067a363fdbb3234fa79b64c83545317b7feec1 AS builder

# Install system packages as root
RUN apk add --no-cache \
    build-base \
    cmake \
    pkgconfig \
    linux-headers \
    libpq libpq-dev

ARG UV_PARAMS="--only-group default  --only-group dev --only-group api --only-group telegram"

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv,id=uv-tests \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project ${UV_PARAMS}

COPY pyproject.toml uv.lock /app/

# Install the project to system Python
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked ${UV_PARAMS}

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

CMD []
ENTRYPOINT []
