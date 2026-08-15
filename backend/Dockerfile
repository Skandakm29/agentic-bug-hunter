# Multi-stage build for backend application
FROM python:3.10-slim AS builder

WORKDIR /workspace

# Install build dependencies if any are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final production stage
FROM python:3.10-slim AS runner

WORKDIR /workspace

# Copy dependencies from builder stage
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy backend codebase preserving parent directory hierarchy
COPY backend/ /workspace/backend/

# Expose FastAPI REST port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["python", "backend/main.py"]
