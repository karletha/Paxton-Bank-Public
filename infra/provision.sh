#!/usr/bin/env bash
# Paxton Bank Prospecting — base infra provisioning (idempotent).
# Creates: BQ datasets, GCS bucket, Firestore named DB, Cloud SQL Postgres.
# Everything is prefixed paxtonprospect- ; safe to re-run.
set -uo pipefail

PROJECT=kairos-504105
REGION=us-east4

echo "== BigQuery datasets =="
for DS in paxtonprospect_core paxtonprospect_audit; do
  bq --project_id=$PROJECT show --dataset $DS >/dev/null 2>&1 \
    && echo "dataset $DS exists" \
    || bq --project_id=$PROJECT mk --dataset --location=US \
         --description "Paxton Bank Prospecting demo ($DS)" $DS
done

echo "== GCS bucket =="
gcloud storage buckets describe gs://$PROJECT-paxtonprospect >/dev/null 2>&1 \
  && echo "bucket exists" \
  || gcloud storage buckets create gs://$PROJECT-paxtonprospect \
       --project=$PROJECT --location=$REGION --uniform-bucket-level-access

echo "== Firestore named database (native, $REGION) =="
gcloud firestore databases describe --database=paxtonprospect --project=$PROJECT >/dev/null 2>&1 \
  && echo "firestore db exists" \
  || gcloud firestore databases create --database=paxtonprospect \
       --location=$REGION --type=firestore-native --project=$PROJECT

echo "== Cloud SQL Postgres (async — takes ~10 min on first run) =="
if gcloud sql instances describe paxtonprospect-sql --project=$PROJECT >/dev/null 2>&1; then
  echo "sql instance exists"
else
  gcloud sql instances create paxtonprospect-sql \
    --project=$PROJECT --region=$REGION \
    --database-version=POSTGRES_16 --edition=enterprise --tier=db-custom-1-3840 \
    --storage-size=20GB --storage-type=SSD \
    --availability-type=zonal --no-backup --async
fi

echo "== provision.sh done (check async SQL op separately) =="
