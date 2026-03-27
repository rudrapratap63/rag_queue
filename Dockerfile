FROM python:3.11-slim

WORKDIR /app

# Ensure you have your dependencies in requirements.txt
# (fastapi, uvicorn, rq, redis)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
