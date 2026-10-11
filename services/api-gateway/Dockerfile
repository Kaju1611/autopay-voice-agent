FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
WORKDIR /app
COPY shared/requirements.txt shared/requirements.txt
RUN pip install --no-cache-dir -r shared/requirements.txt
COPY shared shared
COPY services/api-gateway/api_gateway api_gateway
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "uvloop", "--http", "httptools", "--no-access-log"]
