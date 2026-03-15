output "service_url" {
  description = "URL of the deployed SAHAY Cloud Run service"
  value       = google_cloud_run_v2_service.sahay.uri
}

output "firestore_database_id" {
  description = "Firestore database ID"
  value       = google_firestore_database.sahay.name
}
