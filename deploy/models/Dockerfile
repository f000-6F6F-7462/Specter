# Exports Specter's models to ONNX and NCNN. torch, Ultralytics and pnnx exist only in this image.
FROM python:3.12-slim

# OpenCV, which Ultralytics imports, needs these system libraries.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# The CPU-only build of torch is a fraction of the default download.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision \
    && pip install --no-cache-dir ultralytics==8.4.153 onnx onnxslim onnxruntime pnnx==20260526 ncnn httpx pyyaml

WORKDIR /work
COPY export.py /opt/export/export.py
ENTRYPOINT ["python", "/opt/export/export.py", "--manifest", "/manifest.yaml", "--output", "/models"]
