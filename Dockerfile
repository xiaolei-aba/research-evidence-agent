# CPU-only Docker Space. No model or dataset downloads.
FROM node:24.21.0-bookworm-slim AS frontend
WORKDIR /build
COPY runtime_demo/frontend/package.json runtime_demo/frontend/pnpm-lock.yaml ./
RUN npx --yes pnpm@11.25.0 install --frozen-lockfile
COPY runtime_demo/frontend/ ./
RUN npx --yes pnpm@11.25.0 run build

FROM python:3.11.17-slim-bookworm
RUN useradd --create-home --uid 1000 demo
WORKDIR /app
COPY requirements/demo.lock.txt /app/requirements/demo.lock.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements/demo.lock.txt
COPY --chown=demo:demo runtime_demo/backend /app/runtime_demo/backend
COPY --chown=demo:demo runtime_demo/data /app/runtime_demo/data
COPY --chown=demo:demo LICENSE NOTICE /app/
COPY --chown=demo:demo LICENSES /app/LICENSES
COPY --from=frontend --chown=demo:demo /build/dist /app/runtime_demo/frontend/dist
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DEMO_DEPLOYMENT_MODE=space
USER demo
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=4)"
CMD ["python", "runtime_demo/backend/app.py"]
