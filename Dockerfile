FROM python:3.12-slim

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin trader
WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir -e . \
    && mkdir -p /home/trader/.indstocks \
    && chown -R trader:trader /app /home/trader

USER trader
ENV PYTHONUNBUFFERED=1 \
    INDSTOCKS_TOKEN_CACHE=/home/trader/.indstocks/session_token.json \
    POSITIONS_STORE_PATH=/home/trader/.indstocks/open_positions.json \
    AUDIT_LOG_PATH=/home/trader/.indstocks/audit_trail.jsonl \
    HEALTH_HOST=127.0.0.1 \
    HEALTH_PORT=8080 \
    PAPER_TRADING=true

CMD ["python", "-m", "jev_indstocks_trader.main"]
