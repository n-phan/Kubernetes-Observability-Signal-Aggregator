FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY aggregator/ aggregator/

EXPOSE 8080

CMD ["uvicorn", "aggregator.main:app", "--host", "0.0.0.0", "--port", "8080"]
