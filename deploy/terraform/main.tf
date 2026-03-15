terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── Enable Required APIs ─────────────────────────────────

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "firestore" {
  service            = "firestore.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "aiplatform" {
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

# ── Artifact Registry ────────────────────────────────────

resource "google_artifact_registry_repository" "sahay" {
  location      = var.region
  repository_id = "sahay-repo"
  format        = "DOCKER"
  description   = "SAHAY container images"

  depends_on = [google_project_service.artifactregistry]
}

# ── Firestore Database ───────────────────────────────────

resource "google_firestore_database" "sahay" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  depends_on = [google_project_service.firestore]
}

# ── Cloud Run Service ────────────────────────────────────

resource "google_cloud_run_v2_service" "sahay" {
  name     = var.service_name
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image = var.image_tag

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "4"
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
        name  = "GEMINI_COMPUTER_USE_MODEL"
        value = "gemini-2.5-computer-use-preview-10-2025"
      }
      env {
        name  = "GEMINI_VOICE_MODEL"
        value = "gemini-2.5-flash-native-audio"
      }
      env {
        name  = "FIRESTORE_COLLECTION"
        value = "sahay_tasks"
      }
      env {
        name  = "APP_PORT"
        value = "8080"
      }
      env {
        name  = "SCREEN_WIDTH"
        value = "1440"
      }
      env {
        name  = "SCREEN_HEIGHT"
        value = "900"
      }
      env {
        name  = "DEFAULT_LANGUAGE"
        value = "hi"
      }
      env {
        name  = "BROWSER_HEADLESS"
        value = "true"
      }
    }

    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
    timeout               = "3600s"
  }

  depends_on = [
    google_project_service.run,
    google_artifact_registry_repository.sahay,
  ]
}

# ── IAM: Allow unauthenticated access ────────────────────

resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sahay.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
