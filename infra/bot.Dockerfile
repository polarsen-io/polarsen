# syntax=docker/dockerfile:1.4
# check=error=true
# TODO: drop -rc once 3.15 final images are published
FROM ghcr.io/astral-sh/uv:python3.15-rc-alpine AS builder

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

ARG UV_PARAMS="--only-group default --only-group telegram --only-group llms"

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


FROM python:3.15-rc-alpine AS main

ARG VERSION
ENV VERSION=$VERSION
ARG PROJECT_MODE
ENV PROJECT_MODE=$PROJECT_MODE

RUN addgroup user && \
    adduser -s /bin/bash -D -G user user

# libgcc_s for Rust extensions (pydantic-core) on 3.15 musl
RUN apk add --no-cache libgcc

COPY --from=builder /usr/local/lib/python3.15/site-packages/ /usr/local/lib/python3.15/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

ADD polarsen/ polarsen/

USER user

ENTRYPOINT ["python", "-m", "polarsen", "bot"]

