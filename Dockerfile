FROM python:3.11-slim

# Install system dependencies including coinor-cbc
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    coinor-cbc \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Pre-generate synthetic baseline data and run pipeline
RUN python -m src.data.simulator --seed 42 && \
    python scripts/run_pipeline.py --time-limit 15

# Expose Streamlit default port
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Launch Streamlit dashboard
ENTRYPOINT ["streamlit", "run", "src/dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
