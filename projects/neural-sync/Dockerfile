# Multi-stage build: Node.js for frontend, Python for backend
# The container will run both services via nginx as a reverse proxy

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Build React frontend (Node.js 20)
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

# Copy frontend dependencies and source
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend . .

# Build the frontend (tsc && vite build → dist/)
RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Python FastAPI backend + nginx reverse proxy
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install nginx for reverse proxy
RUN apt-get update && apt-get install -y nginx && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY src ./src
COPY alembic.ini .
COPY alembic ./alembic

# Copy built frontend from Stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# ─────────────────────────────────────────────────────────────────────────────
# Configure nginx to reverse-proxy the FastAPI backend and serve the frontend
# ─────────────────────────────────────────────────────────────────────────────
RUN rm /etc/nginx/sites-enabled/default

RUN cat > /etc/nginx/conf.d/neural-sync.conf <<'EOF'
server {
    listen 8080 default_server;
    server_name _;

    # Serve frontend static files (SPA with fallback to index.html)
    location / {
        root /app/frontend/dist;
        try_files $uri $uri/ /index.html;
        expires 1h;
        add_header Cache-Control "public, immutable";
    }

    # Proxy API requests to the FastAPI backend
    location /api {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# Ensure nginx doesn't daemonize so we can run it in the foreground
RUN sed -i 's/daemon on;/daemon off;/' /etc/nginx/nginx.conf

# ─────────────────────────────────────────────────────────────────────────────
# Environment and startup
# ─────────────────────────────────────────────────────────────────────────────

# Default port (overridden at runtime via -e PORT)
ENV PORT=8080

# Debug mode enabled for local deployments (must override with DEBUG=false in production)
ENV DEBUG=true

# Startup script: run database migrations, start the FastAPI backend in the background, and nginx in the foreground
RUN cat > /app/start.sh <<'EOF'
#!/bin/bash
set -e

# Run Alembic database migrations before starting the backend
echo "Running database migrations..."
alembic upgrade head

# Start the FastAPI backend on port 8000 (internal, not exposed directly)
echo "Starting FastAPI backend on port 8000..."
uvicorn src.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# Give the backend a moment to start
sleep 2

# Start nginx on the public port
echo "Starting nginx on port $PORT..."
nginx -g "daemon off;"

# Cleanup
wait $BACKEND_PID
EOF

RUN chmod +x /app/start.sh

# Expose the public port (nginx listens here; backend is internal)
EXPOSE 8080

# Shell form so $PORT is honored at runtime
CMD bash -c "sed -i \"s/listen 8080/listen \$PORT/\" /etc/nginx/conf.d/neural-sync.conf && /app/start.sh"
