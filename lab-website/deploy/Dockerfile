FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] psycopg[binary]
COPY src /app/src
COPY static /app/static
ENV PYTHONPATH=/app/src
ENV LAB_SNAP_DIR=/app/snapshots
ENV LAB_STATIC_DIR=/app/static
EXPOSE 8080
CMD ["uvicorn", "lab_web.app:app", "--host", "0.0.0.0", "--port", "8080"]
