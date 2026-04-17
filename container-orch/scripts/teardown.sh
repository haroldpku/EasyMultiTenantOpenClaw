#!/bin/bash
# Teardown: stop 3 demo containers but preserve volumes (user data).
# Add --purge to also delete volumes.
set -e
cd "$(dirname "$0")/.."

echo "Stopping containers..."
docker compose down

if [ "$1" = "--purge" ]; then
    echo "Purging volumes..."
    rm -rf volumes/demo01 volumes/demo02 volumes/demo03
    echo "Removing tenants.json..."
    rm -f tenants.json
    echo "Purged."
else
    echo "Volumes preserved in volumes/demo01..03. Use --purge to delete."
fi

echo "Done."
