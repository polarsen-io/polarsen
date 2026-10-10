# syntax=docker/dockerfile:1.4
# check=error=true
# TODO: drop -rc once 3.15 final images are published
FROM ghcr.io/astral-sh/uv:python3.15-rc-alpine as builder

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
