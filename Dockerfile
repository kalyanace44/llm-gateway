FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY prism/ prism/
RUN pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/prism /usr/local/bin/prism
COPY prism/ prism/

# Non-root
RUN adduser --disabled-password --gecos "" prism && chown -R prism:prism /app
USER prism

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["prism", "serve", "--config", "/etc/prism/prism.yaml"]
