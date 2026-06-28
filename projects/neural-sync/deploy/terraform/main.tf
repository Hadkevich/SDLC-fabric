# NEURAL SYNC — GCP infra skeleton (Task04 §7). Cloud Run + Cloud SQL (pgvector) + secrets.
# Skeleton, not a turnkey module: fill in project/region/secret material and `terraform apply`.
terraform {
  required_providers { google = { source = "hashicorp/google", version = "~> 5.0" } }
}

variable "project" { type = string }
variable "region"  { type = string, default = "europe-west1" }

provider "google" {
  project = var.project
  region  = var.region
}

# PostgreSQL with the pgvector extension (enable via: CREATE EXTENSION IF NOT EXISTS vector;)
resource "google_sql_database_instance" "pg" {
  name             = "neural-sync-pg"
  database_version = "POSTGRES_15"
  region           = var.region
  settings {
    tier              = "db-custom-2-7680"
    availability_type = "ZONAL"
    database_flags { name = "cloudsql.enable_pgvector", value = "on" }
  }
  deletion_protection = true
}

# Container image registry
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "neural-sync"
  format        = "DOCKER"
}

# Cloud Run service (deploy the image with: gcloud run services replace ../cloudrun/service.yaml)
resource "google_cloud_run_v2_service" "app" {
  name     = "neural-sync"
  location = var.region
  template {
    scaling { min_instance_count = 1, max_instance_count = 10 }
    containers {
      image = "${var.region}-docker.pkg.dev/${var.project}/neural-sync/neural-sync:latest"
      ports { container_port = 8080 }
    }
  }
}

# Secrets (create versions out-of-band; never commit real values):
#   neural-sync-jwt-secret, neural-sync-database-url, neural-sync-gemini-key
