FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
# pip is not needed at runtime; removing it also removes its vendored libraries from the image.
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip
COPY app.py .
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION PYTHONUNBUFFERED=1
# Non-root numeric UID so Kubernetes runAsNonRoot can verify it.
USER 10001
EXPOSE 8080
# One worker + threads keeps prometheus_client metrics in a single process.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--threads", "8", "--worker-tmp-dir", "/dev/shm", "--no-control-socket", "--graceful-timeout", "20", "app:app"]
