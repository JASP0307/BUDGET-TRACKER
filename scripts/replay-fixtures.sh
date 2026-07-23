#!/usr/bin/env bash
# Replay the real bank-email fixtures into the running app's Postmark inbound
# webhook, so a fresh install's dashboard fills with transactions without
# needing real forwarded mail.
#
# Reads BUDGET_WEBHOOK_SECRET and BUDGET_INBOUND_DOMAIN from .env. The inbound
# token is auto-discovered by logging in as the bootstrap owner (needs
# BUDGET_USER_EMAIL + BUDGET_BOOTSTRAP_PASSWORD in .env) — or pass it as $1.
#
# Usage:
#   scripts/replay-fixtures.sh                 # discover token via login
#   scripts/replay-fixtures.sh <token>         # use an explicit token
#   BASE=http://10.0.0.123:8000 scripts/replay-fixtures.sh
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE:-http://localhost:8000}"
FIXTURES="core/tests/fixtures"

# --- load config from .env (values may contain no spaces; simple parse) ---
get() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
SECRET="${BUDGET_WEBHOOK_SECRET:-$(get BUDGET_WEBHOOK_SECRET)}"
DOMAIN="${BUDGET_INBOUND_DOMAIN:-$(get BUDGET_INBOUND_DOMAIN)}"
[ -n "$SECRET" ] || { echo "no BUDGET_WEBHOOK_SECRET in .env" >&2; exit 1; }

# --- resolve the inbound token ---
TOKEN="${1:-${TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  EMAIL="$(get BUDGET_USER_EMAIL)"; PASS="$(get BUDGET_BOOTSTRAP_PASSWORD)"
  [ -n "$PASS" ] || { echo "pass a token as \$1, or set BUDGET_BOOTSTRAP_PASSWORD" >&2; exit 1; }
  JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT
  curl -s -c "$JAR" -o /dev/null \
    --data-urlencode "email=$EMAIL" --data-urlencode "password=$PASS" "$BASE/login"
  ADDR="$(curl -s -b "$JAR" "$BASE/setup" | grep -oE 'u_[a-z0-9]+@[a-z0-9.]+' | head -1)"
  TOKEN="$(echo "$ADDR" | sed -E 's/^u_([a-z0-9]+)@.*/\1/')"
  [ -n "$TOKEN" ] || { echo "could not discover inbound token via login" >&2; exit 1; }
  echo "discovered inbound token: $TOKEN ($ADDR)" >&2
fi

TO="u_${TOKEN}@${DOMAIN}"
STAMP="$(date +%s)"
echo "replaying fixtures -> $BASE (to: $TO)" >&2

# fixture | sender | subject
ROWS='
popular_consumo.html|notificaciones@popularenlinea.com|Notificacion de Consumo
popular_consumo_usd.html|notificaciones@popularenlinea.com|Notificacion de Consumo
popular_consumo_wrapped.html|notificaciones@popularenlinea.com|Notificacion de Consumo
popular_retiro.html|notificaciones@popularenlinea.com|Notificacion de Retiro
popular_declinada.html|notificaciones@popularenlinea.com|Notificacion de Consumo
qik_purchase.html|notificaciones@qik.do|Usaste tu tarjeta de credito Qik
qik_reversal.html|notificaciones@qik.do|Reverso en tu tarjeta Qik
'

i=0
while IFS='|' read -r fixture sender subject; do
  [ -n "$fixture" ] || continue
  i=$((i+1))
  html="$(cat "$FIXTURES/$fixture")"
  payload="$(jq -n \
    --arg mid "replay-$STAMP-$i" --arg from "$sender" --arg subj "$subject" \
    --arg to "$TO" --arg html "$html" \
    '{MessageID:$mid, From:$from, Subject:$subj,
      ToFull:[{Email:$to}], HtmlBody:$html, Headers:[]}')"
  status="$(curl -s -X POST "$BASE/webhooks/postmark-inbound/$SECRET" \
    -H 'Content-Type: application/json' -d "$payload" | jq -r '.status // "ERR"')"
  printf '  %-28s %-9s -> %s\n' "$fixture" "$subject" "$status"
done <<< "$ROWS"

echo "done ($i fixtures)." >&2
