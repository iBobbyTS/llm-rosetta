FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    WEB_RUN_HOST=0.0.0.0 \
    WEB_RUN_PORT=8080

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && patchright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && useradd --create-home --uid 1000 pwuser \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/* /root/.cache
RUN apt-get update \
    && apt-get install -y --no-install-recommends xauth \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=pwuser:pwuser app.py google_search.py bing_search.py /app/

USER pwuser
EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"

CMD ["sh", "-c", "export HOME=/tmp/pwuser; mkdir -p $HOME; xvfb-run -a uvicorn app:app --host $WEB_RUN_HOST --port $WEB_RUN_PORT --no-access-log"]
