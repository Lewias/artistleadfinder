#!/usr/bin/env bash
# Daily copy of the whole database (accounts, keys, CRM). Keeps the last 14 copies.
# Restore: see README.md, section «Восстановление из копии».

set -euo pipefail
DIR=/opt/alf-server/backups
mkdir -p "$DIR"
chmod 700 "$DIR"
file="$DIR/alf-$(date +%F).dump"
docker exec supabase-db pg_dump -h localhost -U supabase_admin -d postgres -Fc > "$file.part"
mv "$file.part" "$file"
ls -1t "$DIR"/alf-*.dump | tail -n +15 | xargs -r rm -f
echo "$(date -Is) $file $(du -h "$file" | cut -f1)"
