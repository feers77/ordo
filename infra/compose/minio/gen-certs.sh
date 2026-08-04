#!/usr/bin/env bash
# Genera certificado self-signed para MinIO (dev). Ejecutar una vez:
#   bash infra/compose/minio/gen-certs.sh
# Los certificados quedan en infra/compose/minio/certs/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout certs/private.key -out certs/public.crt \
  -subj "/CN=minio" \
  -addext "subjectAltName=DNS:minio,DNS:localhost,IP:127.0.0.1"
chmod 600 certs/private.key
echo "certs generados en $(pwd)/certs"
