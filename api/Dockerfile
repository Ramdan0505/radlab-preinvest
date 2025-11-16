FROM python:3.11-slim
WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    unzip \
    git \
    && rm -rf /var/lib/apt/lists/*

# FastAPI / Chroma client deps
COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# For parsing + embeddings
RUN pip install --no-cache-dir \
      python-evtx \
      python-magic \
      regipy \
      sentence-transformers

# App code
COPY api/main.py /app/main.py
COPY api/embedder.py /app/embedder.py
COPY worker/extract_job.py /app/worker/extract_job.py

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
