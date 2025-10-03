# syntax=docker/dockerfile:1.4
# check=error=true
FROM andarius/python:3.13-alpine AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

ARG UV_PARAMS="--only-group llms --only-group api --only-group default"

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


FROM python:3.13-alpine AS main

ARG version
ENV VERSION=$version
ARG PROJECT_MODE
ENV PROJECT_MODE=$PROJECT_MODE

RUN addgroup user && \
    adduser -s /bin/bash -D -G user user

COPY --from=builder /usr/local/lib/python3.13/site-packages/ /usr/local/lib/python3.13/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

ADD polarsen/ polarsen/

USER user

ENTRYPOINT ["python", "-m", "polarsen", "api"]

