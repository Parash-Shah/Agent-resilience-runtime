FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN addgroup --system runtime && adduser --system --ingroup runtime runtime

COPY pyproject.toml README.md ./
COPY agent_resilience ./agent_resilience
COPY fixtures ./fixtures
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data && chown -R runtime:runtime /app
USER runtime

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"

CMD ["agent-resilience-api"]
