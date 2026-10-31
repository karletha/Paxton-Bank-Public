output "cloud_run_bridge_url" {
  description = "Deployed URL of the Paxton Bank ADK Bridge Cloud Run service"
  value       = google_cloud_run_v2_service.paxton_bridge.uri
}

output "agent_service_account_email" {
  description = "Least-privilege service account email for the Paxton Bank ADK agent"
  value       = google_service_account.paxton_agent_sa.email
}

output "firestore_database_name" {
  description = "Firestore database name storing persistent sessions, compacted history, and HITL approvals"
  value       = google_firestore_database.paxton_firestore.name
}

output "collateral_bucket_name" {
  description = "GCS bucket storing approved Paxton Bank product collateral and call transcripts"
  value       = google_storage_bucket.paxton_collateral.name
}
