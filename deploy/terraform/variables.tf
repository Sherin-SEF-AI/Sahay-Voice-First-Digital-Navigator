variable "project_id" {
  description = "Google Cloud project ID"
  type        = string
}

variable "region" {
  description = "Google Cloud region for deployment"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "sahay"
}

variable "image_tag" {
  description = "Full container image tag (e.g., us-central1-docker.pkg.dev/PROJECT/sahay-repo/sahay:latest)"
  type        = string
}
