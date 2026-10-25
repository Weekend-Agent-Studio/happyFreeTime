#!/usr/bin/env bash
# Optional short maintenance window. Never run against a live-writing volume.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
compose=(docker compose --env-file deploy/compose.env -f compose.yaml)
running=$("${compose[@]}" ps --status running --services)
if ! grep -qx api <<< "$running" || ! grep -qx web <<< "$running"; then
    echo "Both services must be running before this maintenance backup." >&2
    exit 1
fi
api_image=$("${compose[@]}" images -q api | head -n 1)
if [[ -z "$api_image" ]]; then
    echo "Cannot identify the current API image; no services were stopped." >&2
    exit 1
fi
mkdir -p deploy/backups
chmod 700 deploy/backups
backup_dir="$(pwd)/deploy/backups"
# start resumes existing containers; it does not drop the HTTPS overlay or rebuild.
restart_services() { "${compose[@]}" start api web; }
trap restart_services EXIT
"${compose[@]}" stop web api
docker run --rm --network none --read-only --user 0:0 \
    --mount type=volume,source=happyfreetime-data,target=/source,readonly \
    --mount "type=bind,source=$backup_dir,target=/backups" \
    "$api_image" python /app/deploy/backup_volume.py
echo "Snapshot contains temporary conversations/locations. Keep it private and short-lived."
