# ---------------------------------------------------------------------------
# J1939 telemetri / simulasyon servisi
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Bagimliliklar once kopyalanir; kod degisikliginde katman onbellegi korunur.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

# Calisma zamaninda yuklenen arac gorselleri (Arac Ekle paneli) buraya yazilir;
# docker-compose bu dizini frontend ile paylasilan bir volume'e baglar.
RUN mkdir -p /app/uploads

# Kok olmayan kullanici
RUN useradd --create-home --uid 10001 telemetry \
    && chown -R telemetry:telemetry /app
USER telemetry

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=8s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

# ---------------------------------------------------------------------------
# CI icin test katmani:  docker build --target test -t backend-test .
# ---------------------------------------------------------------------------
FROM base AS test
USER root
COPY requirements-dev.txt pytest.ini ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER telemetry
CMD ["python", "-m", "pytest"]
