# ============================================================================
# Paxton Bank AI Sales Companion — Terraform Infrastructure as Code (IaC)
# Provisions Cloud Run v2, Firestore, BigQuery, GCS, Secret Manager, and IAM
# ============================================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# 1. Enable Required Google Cloud APIs
resource "google_project_service" "required_apis" {
  for_each = toset([
    "run.googleapis.com",
    "aiplatform.googleapis.com",
    "firestore.googleapis.com",
    "bigquery.googleapis.com",
    "secretmanager.googleapis.com",
    "dlp.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "artifactregistry.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# 2. Least-Privilege Service Account for the Paxton Companion Agent
resource "google_service_account" "paxton_agent_sa" {
  account_id   = "paxtonprospect-bridge-sa"
  display_name = "Paxton Bank AI Sales Companion Runtime Service Account"
  description  = "Least-privilege identity for Cloud Run ADK Bridge, Vertex AI, Firestore, and Secret Manager"
}

resource "google_project_iam_member" "agent_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/bigquery.dataEditor",
    "roles/secretmanager.secretAccessor",
    "roles/dlp.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.paxton_agent_sa.email}"
}

# 3. Persistent Session & Memory State: Firestore Native Database
resource "google_firestore_database" "paxton_firestore" {
  project                     = var.project_id
  name                        = var.firestore_db_name
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  depends_on                  = [google_project_service.required_apis]
}

# 4. Analytics & Audit Trail: BigQuery Datasets
resource "google_bigquery_dataset" "paxton_core" {
  dataset_id                 = "paxtonprospect_core"
  friendly_name              = "Paxton Bank Core Telemetry & Call Scorecards"
  description                = "Structured call telemetry, BANT scorecards, and tool outcomes"
  location                   = "US"
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.required_apis]
}

resource "google_bigquery_dataset" "paxton_audit" {
  dataset_id                 = "paxtonprospect_audit"
  friendly_name              = "Paxton Bank Regulatory Compliance Audit Logs"
  description                = "Immutable FINRA 2210 / OCC compliance verdicts and HITL approval records"
  location                   = "US"
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.required_apis]
}

# 5. Collateral & Recording Storage: Google Cloud Storage Bucket
resource "google_storage_bucket" "paxton_collateral" {
  name                        = "${var.project_id}-paxton-collateral"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}

# 6. Secure Secret Management: Google Cloud Secret Manager
resource "google_secret_manager_secret" "managed_secrets" {
  for_each = toset([
    "PAXTON_SESSION_KEY",
    "PAXTON_USERS",
    "PAXTON_CRM_API_KEY",
    "PAXTON_ENRICHMENT_API_KEY",
    "PAXTON_SENDGRID_API_KEY",
    "STREAM_API_SECRET",
  ])
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.required_apis]
}

# 7. Cloud Run v2 Service: Paxton Bank ADK Bridge
resource "google_cloud_run_v2_service" "paxton_bridge" {
  name     = "paxtonprospect-bridge"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.paxton_agent_sa.email
    timeout         = "3600s"

    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "FIRESTORE_DB"
        value = google_firestore_database.paxton_firestore.name
      }
      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.paxton_collateral.name
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = "paxtonprospect-bridge"
      }
    }
  }

  depends_on = [
    google_project_service.required_apis,
    google_firestore_database.paxton_firestore,
  ]
}
