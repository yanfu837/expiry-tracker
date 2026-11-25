# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OCR_LANGUAGES=eng+chi_tra
RUN apt-get update && apt-get install -y --no-install-recommends \
    clamav clamav-freshclam tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-tra \
    poppler-utils antiword catdoc libseccomp2 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 processor \
    && mkdir -p /app /var/lib/clamav /var/log/clamav \
    && chown -R processor:processor /app /var/lib/clamav /var/log/clamav
WORKDIR /app
COPY document_service/requirements.txt /app/document_service/requirements.txt
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r document_service/requirements.txt; \
    else pip install --no-cache-dir -r document_service/requirements.txt; fi
COPY --chown=processor:processor document_service /app/document_service
USER processor
EXPOSE 10000
CMD ["python", "-m", "document_service.supervisor"]
