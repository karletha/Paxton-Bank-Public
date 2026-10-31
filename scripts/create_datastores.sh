#!/usr/bin/env bash
# Create the 3 Vertex AI Search datastores (idempotent). Imports run separately
# after world data is loaded.
set -uo pipefail
PROJECT=kairos-504105
TOKEN=$(gcloud auth print-access-token)
BASE="https://discoveryengine.googleapis.com/v1/projects/$PROJECT/locations/global/collections/default_collection"

create() { # id, display, extra-json
  local ID=$1 DISPLAY=$2 EXTRA=$3
  local CODE=$(curl -s -o /tmp/ds_out.json -w "%{http_code}" -X POST \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    "$BASE/dataStores?dataStoreId=$ID" \
    -d "{\"displayName\":\"$DISPLAY\",\"industryVertical\":\"GENERIC\",\"solutionTypes\":[\"SOLUTION_TYPE_SEARCH\"],\"contentConfig\":\"CONTENT_REQUIRED\"$EXTRA}")
  if [ "$CODE" = "200" ]; then echo "created $ID"
  elif grep -q ALREADY_EXISTS /tmp/ds_out.json; then echo "$ID exists"
  else echo "ERROR $ID ($CODE):"; head -c 400 /tmp/ds_out.json; echo; fi
}
create paxtonprospect-content-hub "PaxtonProspect Content Hub (Seismic-sim)" ""
create paxtonprospect-public-sim "PaxtonProspect Public Site Sim" ""
create paxtonprospect-news "PaxtonProspect NewsWire" ""
