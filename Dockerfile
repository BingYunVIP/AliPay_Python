FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt && useradd --uid 10001 --create-home pay
COPY bypay/ ./bypay/
COPY web/ ./web/
COPY --from=frontend /build/dist ./frontend/dist
RUN mkdir -p /app/data /app/secrets && chown -R pay:pay /app/data /app/secrets
USER pay
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "30", "--umask", "0077", "--no-control-socket", "web.wsgi:app"]
