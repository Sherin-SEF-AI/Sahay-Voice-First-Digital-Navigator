#!/bin/bash
# ═══════════════════════════════════════════════════════════
# SAHAY (सहाय) — Automated Cloud Run Deployment
# Usage: ./deploy.sh <PROJECT_ID> [REGION]
# ═══════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_ID="${1:?Usage: ./deploy.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE_NAME="sahay"
REPO_NAME="sahay-repo"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"
IMAGE_TAG="${IMAGE_NAME}:latest"

echo "══════════════════════════════════════════════════"
echo "  SAHAY Deployment"
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Service:  ${SERVICE_NAME}"
echo "══════════════════════════════════════════════════"

# ── Step 1: Enable required APIs ────────────────────────
echo ""
echo "[1/6] Enabling Google Cloud APIs..."
gcloud services enable \
    run.googleapis.com \
    firestore.googleapis.com \
    aiplatform.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    --project="${PROJECT_ID}" \
    --quiet

echo "  APIs enabled."

# ── Step 2: Create Artifact Registry repo ───────────────
echo ""
echo "[2/6] Setting up Artifact Registry..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" \
    --location="${REGION}" \
    --project="${PROJECT_ID}" &>/dev/null; then
    gcloud artifacts repositories create "${REPO_NAME}" \
        --repository-format=docker \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --description="SAHAY container images" \
        --quiet
    echo "  Repository created: ${REPO_NAME}"
else
    echo "  Repository already exists: ${REPO_NAME}"
fi

# ── Step 3: Create Firestore database ──────────────────
echo ""
echo "[3/6] Setting up Firestore..."
if ! gcloud firestore databases describe \
    --project="${PROJECT_ID}" &>/dev/null; then
    gcloud firestore databases create \
        --location="${REGION}" \
        --project="${PROJECT_ID}" \
        --quiet
    echo "  Firestore database created (Native mode)."
else
    echo "  Firestore database already exists."
fi

# ── Step 4: Build Docker image ─────────────────────────
echo ""
echo "[4/6] Building Docker image..."
gcloud builds submit \
    --tag="${IMAGE_TAG}" \
    --project="${PROJECT_ID}" \
    --quiet

echo "  Image built: ${IMAGE_TAG}"

# ── Step 5: Deploy to Cloud Run ────────────────────────
echo ""
echo "[5/6] Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image="${IMAGE_TAG}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --platform=managed \
    --memory=2Gi \
    --cpu=4 \
    --min-instances=0 \
    --max-instances=3 \
    --timeout=3600 \
    --allow-unauthenticated \
    --execution-environment=gen2 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
    --set-env-vars="GOOGLE_CLOUD_LOCATION=${REGION}" \
    --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE" \
    --set-env-vars="GEMINI_COMPUTER_USE_MODEL=gemini-2.5-computer-use-preview-10-2025" \
    --set-env-vars="GEMINI_VOICE_MODEL=gemini-2.5-flash-native-audio" \
    --set-env-vars="FIRESTORE_COLLECTION=sahay_tasks" \
    --set-env-vars="APP_PORT=8080" \
    --set-env-vars="SCREEN_WIDTH=1440" \
    --set-env-vars="SCREEN_HEIGHT=900" \
    --set-env-vars="DEFAULT_LANGUAGE=hi" \
    --set-env-vars="BROWSER_HEADLESS=true" \
    --set-env-vars="GOOGLE_API_KEY=${GOOGLE_API_KEY:-}" \
    --quiet

# ── Step 6: Get deployed URL ──────────────────────────
echo ""
echo "[6/6] Getting service URL..."
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format="value(status.url)")

echo ""
echo "══════════════════════════════════════════════════"
echo "  SAHAY deployed successfully!"
echo "  URL: ${SERVICE_URL}"
echo "══════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo "  1. Visit ${SERVICE_URL} to access the dashboard"
echo "  2. Allow microphone access when prompted"
echo "  3. Click the microphone button and speak your request"
echo ""
