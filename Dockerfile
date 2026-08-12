FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scanner ./scanner
COPY tests ./tests
COPY run_app.py scan.py ./

# Seed universe cache so first scan doesn't have to rebuild it.
COPY data/universe.json /seed/universe.json

ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/var/data \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# On boot: if DATA_DIR has no universe cache yet, copy the seeded one in.
CMD sh -c "mkdir -p $DATA_DIR && \
    [ -f $DATA_DIR/universe.json ] || cp /seed/universe.json $DATA_DIR/universe.json ; \
    exec python -m uvicorn app.main:app --host $HOST --port $PORT"
