#!/usr/bin/env bash
# Back up the SQLite database safely (online .backup) from the running container.
# Usage: scripts/backup.sh [output_dir]
# Schedule via host cron, e.g. daily:  0 3 * * *  /path/to/scripts/backup.sh /backups
set -euo pipefail

OUT_DIR="${1:-./backups}"
SERVICE="${APN_SERVICE:-app}"
DB_PATH="${APN_DB_PATH:-/data/digest.db}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$OUT_DIR"
DEST="$OUT_DIR/digest-$STAMP.db"

# Use SQLite's online backup so it's consistent even while the app is writing.
docker compose exec -T "$SERVICE" \
  python -c "import sqlite3,sys; src=sqlite3.connect('$DB_PATH'); dst=sqlite3.connect('/tmp/backup.db'); src.backup(dst); dst.close(); src.close()"
docker compose cp "$SERVICE:/tmp/backup.db" "$DEST"
docker compose exec -T "$SERVICE" rm -f /tmp/backup.db

echo "Backup written to $DEST"

# Optional retention: keep the 30 most recent.
ls -1t "$OUT_DIR"/digest-*.db 2>/dev/null | tail -n +31 | xargs -r rm -f
