#!/bin/sh
set -e

API="${BABO_ADMIN_API:-https://api.babo.agency/api}"
API_ESC=$(printf '%s' "$API" | sed "s/'/\\\\'/g")

# Inject before Angular boot so environment.prod reads window.__BABO_ADMIN_API__
if ! grep -q '__BABO_ADMIN_API__' /app/browser/index.html; then
  sed -i "s|<head>|<head><script>window.__BABO_ADMIN_API__='${API_ESC}';</script>|" /app/browser/index.html
fi

exec serve /app/browser -s -l "${PORT:-3000}"
