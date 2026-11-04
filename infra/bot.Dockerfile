# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
# check=error=true
FROM ghcr.io/astral-sh/uv:python3.13-alpine@sha256:63b8fa4be2338728034164eb6c067a363fdbb3234fa79b64c83545317b7feec1 AS builder

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1
ENV UV_PROJECT_ENVIRONMENT=/usr/local

ARG UV_PARAMS="--only-group default --only-group telegram --only-group llms"
ARG PROJECT_MODE

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

ENTRYPOINT ["python", "-m", "polarsen", "bot"]

