# syntax=docker/dockerfile:1
#   Build it from the repository root:
#   docker build -f deploy/Dockerfile .

# The Python runtime the models need is chosen here, per hardware profile:
#   onnxruntime  pc-cpu, pc-nvidia (GPU also needs a CUDA base image), jetson
#   ncnn         raspberry-pi
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
ARG SPECTER_EXTRA=onnxruntime

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --extra "${SPECTER_EXTRA}"

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra "${SPECTER_EXTRA}"

FROM ${PYTHON_IMAGE}
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --create-home specter \
    && install -d -o specter -g specter -m 0755 /var/lib/specter /opt/specter/models \
    && install -d -o specter -g specter -m 0700 /etc/specter

COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER specter
ENTRYPOINT ["specter"]
