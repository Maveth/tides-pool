FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY sql ./sql

RUN pip install --no-cache-dir -e .

ENV TIDES_HOST=0.0.0.0
ENV TIDES_PORT=8080

RUN mkdir -p /app/data

EXPOSE 8080 28916
CMD ["tides-pool", "serve", "--host", "0.0.0.0", "--port", "8080"]

