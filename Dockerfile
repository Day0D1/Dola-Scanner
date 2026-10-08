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
COPY bigsam ./bigsam

# BigSam Alerts (mounted at /bigsamalerts). BIGSAM_* settings win over Dola's own env vars, so
# Telegram/secrets never fall back to Dola's. Secrets go in the Render dashboard:
#   BIGSAM_ADMIN_PASSWORD, BIGSAM_TELEGRAM_BOT_TOKEN, BIGSAM_TELEGRAM_CHAT_ID
ENV BIGSAM_TELEGRAM_BOT_TOKEN="" \
    BIGSAM_TELEGRAM_CHAT_ID="" \
    BIGSAM_SECRET_KEY="" \
    BIGSAM_PUBLIC_URL="https://dola-scanner.onrender.com/bigsamalerts" \
    BIGSAM_ACCOUNT_SIZE=2500 \
    BIGSAM_MAX_DRAWDOWN_PCT=4 \
    BIGSAM_LTF_TIMEFRAMES="15m,1h" \
    BIGSAM_MIN_BOS=2 \
    BIGSAM_CHAIN_POI=reversal \
    BIGSAM_RR_TARGET=3 \
    BIGSAM_PARTIAL_AT_R=1.5 \
    BIGSAM_POI_PREFERENCE=order_block \
    BIGSAM_PROP_MODE=true

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
