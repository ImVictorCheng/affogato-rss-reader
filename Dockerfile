# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web-builder
WORKDIR /src/web
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM python:3.14.6-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS wheel-builder
WORKDIR /src
RUN python -m pip install --no-cache-dir build==1.3.0
COPY README.md LICENSE ./
COPY backend/ ./backend/
RUN rm -rf ./backend/static && mkdir -p ./backend/static
COPY --from=web-builder /src/web/dist/ ./backend/static/
RUN python -m build --wheel --outdir /wheels ./backend

FROM python:3.14.6-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS runtime
ARG VERSION=0.3.1
ARG VCS_REF=unknown
ARG SOURCE_URL=https://github.com/OWNER/affogato-rss-reader
LABEL org.opencontainers.image.title="Affogato RSS Reader" \
      org.opencontainers.image.description="Private self-hosted RSS and Atom reader" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="${SOURCE_URL}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AFFOGATO_RSS_READER_DATA_DIR=/app/data \
    AFFOGATO_RSS_READER_STATIC_DIR=/usr/local/lib/python3.14/site-packages/backend/static
RUN addgroup -S -g 10001 reader \
    && adduser -S -D -H -u 10001 -G reader -s /sbin/nologin reader \
    && mkdir -p /app/data /app/secrets /app/logs \
    && chown reader:reader /app/data /app/secrets /app/logs
COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir -r /tmp/requirements.lock
COPY --from=wheel-builder /wheels/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-deps /tmp/*.whl \
    && rm -rf /tmp/*.whl /tmp/requirements.lock
COPY LICENSE /usr/share/licenses/affogato-rss-reader/LICENSE
USER reader
WORKDIR /app
EXPOSE 8787
VOLUME ["/app/data", "/app/secrets"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/v1/health', timeout=3).read()"]
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers"]
