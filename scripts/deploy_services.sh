#!/usr/bin/env bash
# Deploy all sim services to Cloud Run (source deploys; idempotent).
set -uo pipefail
cd "$(dirname "$0")/.."
./scripts/sync_common.sh
SA=paxtonprospect-sims@kairos-504105.iam.gserviceaccount.com
REGION=us-east4
PUBLIC="newswire content-hub"
deploy() {
  local name=$1; shift
  echo "=== deploying paxtonprospect-$name ==="
  gcloud run deploy paxtonprospect-$name --source services/$name --region $REGION \
    --service-account $SA --memory 512Mi --max-instances 2 --quiet "$@" 2>&1 | tail -2
}
for s in newswire content-hub; do deploy $s --no-allow-unauthenticated & done
for s in kyc market-data directory scheduler; do deploy $s --no-allow-unauthenticated & done
wait
deploy crm-sim --no-allow-unauthenticated \
  --add-cloudsql-instances kairos-504105:us-east4:paxtonprospect-sql \
  --set-secrets CRM_DB_PASS=paxtonprospect-crm-db-pass:latest
echo "ALL SERVICE DEPLOYS DONE"
