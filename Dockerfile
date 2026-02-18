FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py apple_jws.py ./

# Data volume for SQLite persistence
VOLUME ["/app/data"]
ENV DATABASE_PATH=/app/data/sync.db

EXPOSE 6413

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6413"]
