# Plain OCI — no BuildKit-specific syntax, so `podman build` and `docker build`
# produce the same image. Local builds default to Podman (see the Makefile); CI
# uses Docker because that is what GitHub-hosted runners ship with.
#
# Multi-stage: wheels are built in the builder, so the runtime image carries no
# compiler toolchain and nothing writable that the app doesn't need. The final
# image runs as an unprivileged user with a read-only-friendly layout, which is
# what lets the Cloud Run service run without extra capabilities.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080

RUN groupadd --system --gid 1001 sentinel \
    && useradd --system --uid 1001 --gid sentinel --no-create-home sentinel

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv
COPY --chown=sentinel:sentinel app ./app

USER sentinel
EXPOSE 8080

# Cloud Run terminates TLS and load-balances, so a single uvicorn worker per
# container plus autoscaling on concurrency is the right shape here — adding
# in-container workers just multiplies cold-start memory.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 65
