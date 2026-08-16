FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY prism/ prism/
COPY prism_cloud/ prism_cloud/

RUN pip install --no-cache-dir build && \
    pip install --no-cache-dir . && \
    pip install --no-cache-dir numpy

FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY prism/ prism/
COPY prism_cloud/ prism_cloud/

ENV PRISM_CONFIG=/etc/prism/prism.yaml
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["uvicorn", "prism.proxy.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
