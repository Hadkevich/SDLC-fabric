FROM python:3.11-slim

WORKDIR /app

# Copy dependency manifest first for layer caching
COPY requirements.txt .

# Install Python dependencies (sentence-transformers pulls torch — can be slow)
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Default port — overridden at runtime via -e PORT=<n>
ENV PORT=8080

# Local pilot runs in debug mode so the JWT-secret startup guard allows the
# default dev secret to be used without a RuntimeError.  Production deployments
# MUST override both of these:
#   -e DEBUG=false
#   -e NEURAL_SYNC_JWT_SECRET=<cryptographically-random-value>
ENV DEBUG=true

# Shell form so $PORT is expanded at container start
CMD uvicorn src.main:app --host 0.0.0.0 --port $PORT
