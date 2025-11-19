FROM python:3.11-slim

# Create app dirs
WORKDIR /app
RUN mkdir -p /app/static /app/worker /data /data/artifacts

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install API requirements
COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy API source code
COPY api/main.py /app/main.py
COPY api/embedder.py /app/embedder.py

# Copy worker
COPY worker/extract_job.py /app/worker/extract_job.py

# COPY STATIC FILES INTO THE IMAGE  <<<< THIS IS THE FIX
COPY static/ /app/static/

# Run API
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
