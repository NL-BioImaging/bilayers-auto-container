# Fully qualified, so engines without short-name resolution (Podman) build it as Docker does
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# >>> tool layer: install the wrapped workflow, pinned (filled in per workflow)
# <<< tool layer

# --- bilayers layer: spec parsing, OME-Zarr conversion, RO-Crate ---
# If the tool needs another Python, install this into its own environment instead,
# e.g. python3.12 -m venv /opt/bilayers && /opt/bilayers/bin/pip install ..., and use it in ENTRYPOINT.
COPY pyproject.toml README.md LICENSE /opt/bilayers-auto/
COPY src /opt/bilayers-auto/src
RUN pip install /opt/bilayers-auto

# folder (in the build context) holding this workflow's config.yaml
ARG WORKFLOW_DIR=.
COPY ${WORKFLOW_DIR}/config.yaml wrapper.py /app/
RUN mkdir -p /data/in /data/out
WORKDIR /app
ENTRYPOINT ["python", "/app/wrapper.py"]
