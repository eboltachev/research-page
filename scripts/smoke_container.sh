#!/usr/bin/env bash
set -euo pipefail

docker compose up --build -d
trap 'docker compose down -v' EXIT

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done

echo "Service failed smoke check" >&2
exit 1
