FROM python:3.11-slim
WORKDIR /app

# System deps for libmagic
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 unzip && rm -rf /var/lib/apt/lists/*

COPY extract_job.py /app/

# Python deps
RUN pip install --no-cache-dir python-magic python-evtx

CMD ["tail", "-f", "/dev/null"]
