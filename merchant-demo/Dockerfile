FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home demo
COPY app.py pay_http.py wsgi.py ./
COPY static ./static
RUN mkdir /app/data /app/secrets && chown -R demo:demo /app/data /app/secrets
USER demo
CMD ["gunicorn", "--bind", "0.0.0.0:8100", "--workers", "2", "--threads", "2", "--umask", "0077", "--no-control-socket", "wsgi:app"]
