# NEURAL SYNC — Deployment (Task04 §7 Infra)

## Why & where
- **Why:** a live URL for the jury demo + the §7 "GCP" / §8 scale story, without standing up a
  bespoke production cluster.
- **Where (primary, manifested):** **GCP Cloud Run + Cloud SQL for PostgreSQL (pgvector)** —
  Cloud Run is the closest managed equivalent to "deploy to GCP", autoscales for 10k+ load, and
  Cloud SQL keeps the exact pgvector contract used locally (so GDPR's atomic erasure cascade and
  the ANN `<=>` queries behave identically). Manifests: [`deploy/cloudrun/service.yaml`](deploy/cloudrun/service.yaml),
  Terraform skeleton: [`deploy/terraform/main.tf`](deploy/terraform/main.tf).
- **Where (alt, fastest live URL):** Render (managed Postgres w/ pgvector + web service + static
  site) — one-click-ish for a demo; documented below.

## Local (works today)
```bash
docker compose up        # backend :8000, postgres+pgvector, frontend :5173
```

## GCP Cloud Run
```bash
# 0. prerequisites: a GCP project, gcloud auth, Artifact Registry repo (terraform creates it)
cd deploy/terraform && terraform init && terraform apply -var project=YOUR_PROJECT   # creates Cloud SQL + repo + service

# 1. enable pgvector on the Cloud SQL instance, then run migrations
psql "$DATABASE_URL_SYNC" -c "CREATE EXTENSION IF NOT EXISTS vector;"
alembic upgrade head

# 2. build + push the image (multi-stage Dockerfile: React build → FastAPI + nginx on $PORT=8080)
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT/neural-sync/neural-sync:latest

# 3. create secrets (never inline them): JWT secret, async DATABASE_URL, GEMINI_API_KEY
#    -> Secret Manager: neural-sync-jwt-secret / neural-sync-database-url / neural-sync-gemini-key

# 4. deploy
gcloud run services replace deploy/cloudrun/service.yaml --region=REGION
```
The startup guard refuses to boot in `DEBUG=false` with the default JWT secret or a mismatched
`EMBEDDING_DIM`, so a misconfigured deploy fails fast instead of running insecure/broken.

## Render (alternative, quick live URL)
1. Create a **PostgreSQL** instance; run `CREATE EXTENSION vector;` + `alembic upgrade head`.
2. **Web Service** from the repo `Dockerfile` (listens on `$PORT`); set env `DATABASE_URL`,
   `NEURAL_SYNC_JWT_SECRET`, `GEMINI_API_KEY`, `DEBUG=false`.
3. (Optional) **Static Site** for `frontend/` (`npm run build` → `frontend/dist`) with
   `VITE_API_BASE_URL` pointing at the web service.

## Notes / honest status
- The ingestion feature's pipeline deploy + Playwright e2e were **sandbox-limited in CI** (Docker
  build blocked, browser MCP not granted) — re-verify in a Docker+Playwright environment. See
  [`../../EVALUATION.md`](../../EVALUATION.md).
- The multi-stage `Dockerfile` (devops-agent output) is **not yet built/verified**; build it in a
  Docker-capable env before relying on it for production.
