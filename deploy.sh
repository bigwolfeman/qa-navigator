#!/usr/bin/env bash
# Quick deploy to Cloud Run. For interactive setup, use: ./setup.sh
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="qa-navigator"
REPO="cloud-run-builds"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE_NAME}:latest"

echo "==> Creating Artifact Registry repo (if needed)"
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT_ID" 2>/dev/null || true

echo "==> Building container"
gcloud builds submit \
  --tag "$IMAGE" \
  --project "$PROJECT_ID"

echo "==> Deploying to Cloud Run"
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --memory 2Gi \
  --cpu 2 \
  --timeout 600 \
  --set-env-vars "GOOGLE_API_KEY=${GOOGLE_API_KEY:?Set GOOGLE_API_KEY}" \
  --allow-unauthenticated

URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" \
  --format='value(status.url)')

echo "==> Deployed: $URL"
echo "==> Health check: curl $URL/health"
