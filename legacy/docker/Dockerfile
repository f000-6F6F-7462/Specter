# CPU-only app image for Linux machines. Model inference here always runs on
# CPUExecutionProvider (onnxruntime) / device="cpu" (torch) — no MPS/CoreML. On Apple Silicon,
# run `specter ingest` / `specter-enroll` natively in a uv/venv instead, for MPS/CoreML.

FROM python:3.14-slim-trixie AS builder

RUN apt-get update && apt-get install --no-install-recommends -y \
    build-essential \
    pkg-config \
    meson \
    ninja-build \
    libgirepository-2.0-dev \
    gobject-introspection \
    libcairo2-dev \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
# torch first, from the CPU-only index — ultralytics otherwise pulls the CUDA build
# (several GB of nvidia_* wheels) which is dead weight with no GPU in the container.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir ".[ml,media,media-pyav,webrtc]"

FROM python:3.14-slim-trixie AS runtime

RUN apt-get update && apt-get install --no-install-recommends -y \
    libgirepository-2.0-0 \
    libcairo2 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-libav \
    gir1.2-gstreamer-1.0 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin specter
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    SPECTER_MODELS__DETECTOR__DEVICE=cpu

WORKDIR /home/specter
COPY --chown=specter:specter alembic ./alembic
COPY --chown=specter:specter alembic.ini ./alembic.ini

USER specter

ENTRYPOINT ["specter"]
CMD ["api"]
