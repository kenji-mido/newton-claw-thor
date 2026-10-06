# newton-claw on DGX Spark (GB10, aarch64, CUDA 13) — the setup_thor.sh venv, inside a container.
# The repo itself is bind-mounted at /workspace (see compose.yaml); this image only holds the runtime.
FROM nvidia/cuda:13.0.3-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
# libpython3.12: usd-exchange (Newton's pxr provider on aarch64) links against it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv libpython3.12 git ca-certificates curl \
        libgl1 libegl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Run as the host user (uid 1000) so logs/checkpoints written to the bind mount stay owned by them.
# ubuntu:24.04 already has "ubuntu" at uid 1000 — reuse it.
ENV HOME=/home/ubuntu \
    VIRTUAL_ENV=/opt/venv \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/tmp/uv-cache
RUN install -d -o 1000 -g 1000 /opt/venv /home/ubuntu/.cache && chown -R 1000:1000 /home/ubuntu

COPY requirements-thor.txt /tmp/requirements-thor.txt
USER 1000:1000
RUN --mount=type=cache,target=/tmp/uv-cache,uid=1000,gid=1000 \
    uv venv --python /usr/bin/python3.12 /opt/venv && \
    uv pip install -p /opt/venv -r /tmp/requirements-thor.txt \
        --extra-index-url https://download.pytorch.org/whl/cu130 --index-strategy unsafe-best-match

# `claw open` runs `python -m newton.examples <robot>`, which has no port flag, so its browser viewer
# used ViewerViser's default :8080 (taken by OpenShell on this host) instead of :8090 like train/play.
# Make that default follow NEWTON_VISER_PORT; fail the build if Newton's source no longer matches.
RUN f=/opt/venv/lib/python3.12/site-packages/newton/_src/viewer/viewer_viser.py && \
    grep -q '        port: int = 8080,' "$f" && \
    sed -i 's/        port: int = 8080,/        port: int = int(os.environ.get("NEWTON_VISER_PORT", "8090")),/' "$f"

ENV PATH=/opt/venv/bin:/workspace/agents/openclaw:$PATH \
    NEWTON_PYTHON=/opt/venv/bin/python \
    NEWTON_CLAW_DIR=/workspace \
    NEWTON_DEFAULT_VIEWER=viser \
    PYTHONUNBUFFERED=1
WORKDIR /workspace
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/newton-claw-entrypoint
EXPOSE 5561 8090
# Control server + optional `claw $AUTOSTART` job (docker/entrypoint.sh); same in Docker and Kubernetes.
CMD ["newton-claw-entrypoint"]
