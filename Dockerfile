# A container for the read-only service. It serves results; it does not compute them, so
# it needs no GPU, no credentials and no network access of its own.
#
#   docker build -t complex-terrain-verif .
#   docker run --rm -p 8000:8000 -v "$PWD/results:/data/results:ro" complex-terrain-verif
#
# The results directory is mounted read-only on purpose: the service has no write path and
# the container should not be able to acquire one.

FROM python:3.12-slim AS base

# Fail loudly rather than silently buffering a traceback nobody sees.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AFV_RESULTS_ROOT=/data/results

WORKDIR /app

# Dependencies first, so a source change does not reinstall the world.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"

# Run as a non-root user. A read-only service has no reason to be root, and a container
# that is root by default is one that stays root when somebody adds a volume.
RUN useradd --create-home --uid 10001 afv \
    && mkdir -p /data/results \
    && chown -R afv:afv /data
USER afv

EXPOSE 8000

# The health endpoint reports the results root and whether it exists, which is the first
# thing to be wrong when a volume is mounted at the wrong path.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "afv.server:app", "--host", "0.0.0.0", "--port", "8000"]
