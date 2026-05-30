FROM python:3.11-slim

LABEL maintainer="Nagizaaz Shaik <shaikn6@udayton.edu>"
LABEL description="HIPAA-compliant clinical NLP LLMOps pipeline"

# System dependencies for spaCy / Presidio
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Copy application code
COPY . .

# Default environment
ENV MOCK_MODE=true
ENV MOCK_S3=true
ENV DATABASE_URL=sqlite:///./clinical_llmops.db
ENV S3_AUDIT_BUCKET=hipaa-audit-logs

# Create docs directory for screenshots
RUN mkdir -p docs/screenshots

# Expose ports
EXPOSE 8000 8501

# Default: run FastAPI
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
