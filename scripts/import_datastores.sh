#!/usr/bin/env bash
# Import world data into the 3 Vertex AI Search datastores (idempotent, full sync).
set -uo pipefail
PROJECT=kairos-504105
TOKEN=$(gcloud auth print-access-token)
BASE="https://discoveryengine.googleapis.com/v1/projects/$PROJECT/locations/global/collections/default_collection/dataStores"

import_gcs() { # datastore, gcs-pattern
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    "$BASE/$1/branches/default_branch/documents:import" \
    -d "{\"gcsSource\":{\"inputUris\":[\"$2\"],\"dataSchema\":\"content\"},\"reconciliationMode\":\"FULL\"}" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print('$1 import op:', d.get('name', d))"
}
import_gcs paxtonprospect-content-hub "gs://$PROJECT-paxtonprospect/world_txt/content_assets/*.txt"
import_gcs paxtonprospect-public-sim "gs://$PROJECT-paxtonprospect/world_txt/public_site/*.txt"

# NewsWire: BigQuery structured import
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "$BASE/paxtonprospect-news/branches/default_branch/documents:import" \
  -d "{\"bigquerySource\":{\"projectId\":\"$PROJECT\",\"datasetId\":\"paxtonprospect_core\",\"tableId\":\"news\",\"dataSchema\":\"custom\"},\"reconciliationMode\":\"FULL\",\"idField\":\"article_id\"}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('paxtonprospect-news import op:', d.get('name', d))"
