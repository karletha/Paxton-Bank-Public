variable "project_id" {
  description = "Google Cloud Project ID for Paxton Bank AI Sales Companion"
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region for Cloud Run, Vertex AI, and Firestore"
  type        = string
  default     = "us-central1"
}

variable "firestore_db_name" {
  description = "Firestore Native database identifier for persistent session and memory state"
  type        = string
  default     = "paxtonprospect"
}

variable "container_image" {
  description = "Artifact Registry container image URI for the Paxton Bank ADK Bridge"
  type        = string
  default     = "us-central1-docker.pkg.dev/paxton-bank-ai/paxton/paxtonprospect-bridge:latest"
}
