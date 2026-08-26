FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .

# Install the CPU-only torch build first. The default PyPI linux wheel pulls
# several GB of nvidia-* CUDA packages that are dead weight on a GPU-less
# host. Doing this before requirements.txt means the torch>=2.1.0 pin below is
# already satisfied and pip will not re-resolve it to the CUDA build.
# requirements.txt is deliberately left alone so local development on macOS
# keeps resolving its own platform wheel.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch

RUN pip install --no-cache-dir -r requirements.txt

# Download NLP data at build time
RUN python -m spacy download en_core_web_sm && \
    python -c "import nltk; nltk.download('vader_lexicon', quiet=True)"

# Copy source code
COPY . .

RUN mkdir -p data/raw data/processed models logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.api.routes.main:app", "--host", "0.0.0.0", "--port", "8000"]
