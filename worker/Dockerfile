FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 unzip libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY worker /app/worker

CMD ["sleep", "999999999"]
