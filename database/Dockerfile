FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY shared/requirements.txt shared/requirements.txt
RUN pip install --no-cache-dir -r shared/requirements.txt
COPY shared shared
COPY database database
CMD ["sh", "-c", "alembic -c database/alembic.ini upgrade head && python -m database.seed.seed"]