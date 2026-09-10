FROM python:3.11-slim

WORKDIR /app

# System dependencies for PDF, image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    fonts-dejavu \
    fonts-freefont-ttf \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8001/api/health || exit 1

# --proxy-headers: χωρίς αυτό το request.client.host είναι πάντα η IP του nginx, οπότε
# οι IP των ψηφιακών υπογραφών και το rate-limit ανά IP γίνονται άχρηστα.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
