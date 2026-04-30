#!/usr/bin/env bash
# Trading-Bot-v2 — End-to-end health check
#
# Verifies every layer of the stack:
#   - Docker containers running and healthy
#   - Postgres reachable, schema applied
#   - API REST endpoints + WebSocket server
#   - Nginx reverse-proxy routing
#   - Bot subprocess running, scheduler active, no error spam
#   - Telemetry pipeline (bot -> API -> Postgres) writing recent events
#   - Alpaca connectivity (account, market data)
#   - Disk usage and log rotation sanity
#
# Usage:   ./scripts/healthcheck.sh
# Exit:    0 if all checks pass, 1 otherwise

set -uo pipefail

# Resolve project root (script lives in scripts/)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------- pretty output ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

section() {
  echo ""
  echo -e "${BOLD}${BLUE}== $1 ==${NC}"
}

pass() {
  echo -e "  ${GREEN}✓${NC} $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

warn() {
  echo -e "  ${YELLOW}⚠${NC} $1"
  WARN_COUNT=$((WARN_COUNT + 1))
}

fail() {
  echo -e "  ${RED}✗${NC} $1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

dim() {
  echo -e "    ${DIM}$1${NC}"
}

# ---------- helpers ----------

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing required command: $1"
    return 1
  fi
}

# Run a docker-compose service command, returning its output
compose_exec() {
  docker compose exec -T "$@"
}

# Compose ports from docker-compose.yml
NGINX_HOST="localhost"
NGINX_PORT="3200"
BASE_URL="http://${NGINX_HOST}:${NGINX_PORT}"

# ===================================================================
# Pre-flight: required commands
# ===================================================================
section "Pre-flight"

require_cmd docker || exit 1
require_cmd curl || exit 1
if command -v jq >/dev/null 2>&1; then
  HAS_JQ=1
  pass "docker, curl, jq available"
else
  HAS_JQ=0
  warn "jq not installed — JSON output will be raw"
fi

# ===================================================================
# Container status
# ===================================================================
section "Containers"

EXPECTED_SERVICES="postgres api dashboard bot nginx"
for svc in $EXPECTED_SERVICES; do
  STATUS="$(docker compose ps --format json "$svc" 2>/dev/null | grep -oE '"State":"[^"]+"' | head -1 | cut -d'"' -f4)"
  HEALTH="$(docker compose ps --format json "$svc" 2>/dev/null | grep -oE '"Health":"[^"]+"' | head -1 | cut -d'"' -f4)"
  if [ "$STATUS" = "running" ]; then
    if [ -z "$HEALTH" ] || [ "$HEALTH" = "healthy" ]; then
      pass "$svc: running${HEALTH:+ ($HEALTH)}"
    else
      warn "$svc: running but health=$HEALTH"
    fi
  else
    fail "$svc: state=$STATUS"
  fi
done

# ===================================================================
# Nginx proxy (port 3200)
# ===================================================================
section "Nginx"

if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE_URL/health" | grep -q "^200"; then
  pass "Nginx proxying /health -> api"
else
  fail "Nginx /health returned non-200 (try 'docker compose logs nginx')"
fi

if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$BASE_URL/" | grep -q "^200"; then
  pass "Nginx proxying / -> dashboard"
else
  fail "Nginx / returned non-200"
fi

# ===================================================================
# API health and endpoints
# ===================================================================
section "API (REST)"

HEALTH_RESP="$(curl -s --max-time 5 "$BASE_URL/health" || echo "")"
if echo "$HEALTH_RESP" | grep -q '"ok":true'; then
  TS_MS="$(echo "$HEALTH_RESP" | grep -oE '"ts":[0-9]+' | head -1 | cut -d':' -f2)"
  pass "/health: ok, ts=$TS_MS"
else
  fail "/health: did not return ok=true (got: $HEALTH_RESP)"
fi

# Routes that must always respond 200 with JSON (even if data is null/empty)
for route in \
  "/api/sessions/today" \
  "/api/sessions" \
  "/api/scanner/latest" \
  "/api/scanner/watchlist" \
  "/api/trades" \
  "/api/trades/today" \
  "/api/signals" \
  "/api/positions" \
  "/api/events" \
  "/api/performance/summary" \
  "/api/performance/equity-curve" \
  "/api/performance/streaks" \
  "/api/gates/latest" \
  "/api/config"
do
  CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${BASE_URL}${route}")"
  if [ "$CODE" = "200" ]; then
    pass "$route → 200"
  else
    fail "$route → HTTP $CODE"
  fi
done

# Alpaca-backed routes (need creds in .env)
for route in \
  "/api/portfolio/account" \
  "/api/portfolio/positions" \
  "/api/portfolio/history?period=1D&timeframe=5Min" \
  "/api/market/bars?symbol=SPY&timeframe=5Min&limit=10"
do
  CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "${BASE_URL}${route}")"
  case "$CODE" in
    200) pass "$route → 200 (Alpaca call succeeded)" ;;
    503) fail "$route → 503 — ALPACA_API_KEY/SECRET missing in .env" ;;
    *)   fail "$route → HTTP $CODE" ;;
  esac
done

# ===================================================================
# WebSocket connectivity
# ===================================================================
section "WebSocket"

# Basic check: nginx returns 101/426 on /ws (WS upgrade required)
WS_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "$BASE_URL/ws" || echo "000")"
case "$WS_CODE" in
  101) pass "/ws upgrades to WebSocket (HTTP 101)" ;;
  400|426) warn "/ws responded $WS_CODE (server reachable but rejected upgrade — probably curl handshake quirk, not a real failure)" ;;
  *)   fail "/ws → HTTP $WS_CODE" ;;
esac

# ===================================================================
# Postgres / schema
# ===================================================================
section "Postgres"

if docker compose ps --format json postgres 2>/dev/null | grep -q '"State":"running"'; then
  TABLES="$(compose_exec postgres psql -U "${POSTGRES_USER:-trading}" -d tradingbot -tA -c \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$TABLES" ] && [ "$TABLES" -gt 0 ]; then
    pass "Postgres up, $TABLES public tables present"
  else
    fail "Postgres up but schema empty (prisma db push didn't run?)"
  fi

  # Sample row counts for the busiest tables
  for t in TradingSession Trade Signal ScanResult BotEvent EquitySnapshot; do
    COUNT="$(compose_exec postgres psql -U "${POSTGRES_USER:-trading}" -d tradingbot -tA -c \
      "SELECT count(*) FROM \"$t\";" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$COUNT" ]; then
      dim "$t: $COUNT row(s)"
    fi
  done
else
  fail "Postgres container not running"
fi

# ===================================================================
# Bot — process + recent activity
# ===================================================================
section "Bot"

BOT_LOGS_RECENT="$(docker compose logs --tail 200 --since 5m bot 2>/dev/null || echo "")"

if [ -n "$BOT_LOGS_RECENT" ]; then
  pass "Bot logs accessible"

  # Look for the steady-state heartbeat patterns
  if echo "$BOT_LOGS_RECENT" | grep -q "Scheduler running\|Scan complete\|Bootstrap"; then
    pass "Bot scheduler showing recent activity"
  else
    warn "No 'Scheduler' or 'Scan complete' in last 5min of bot logs (idle or stuck?)"
  fi

  # Surface error patterns
  ERRORS="$(echo "$BOT_LOGS_RECENT" | grep -iE "error|traceback|exception" | grep -vE "0 errors|no error" | head -10)"
  if [ -z "$ERRORS" ]; then
    pass "No ERROR/Traceback in last 5min"
  else
    ERR_COUNT="$(echo "$ERRORS" | wc -l | tr -d ' ')"
    warn "$ERR_COUNT recent error line(s) in bot logs"
    echo "$ERRORS" | head -3 | while read -r line; do dim "${line:0:120}"; done
  fi

  # Specific known-bad markers
  if echo "$BOT_LOGS_RECENT" | grep -q "connection limit exceeded"; then
    fail "Alpaca 'connection limit exceeded' detected — ghost WS connection may be holding the slot"
  fi
  if echo "$BOT_LOGS_RECENT" | grep -q "Missing required environment"; then
    fail "Bot is missing env vars (likely ALPACA_API_KEY/SECRET in .env)"
  fi
else
  fail "Cannot read bot logs"
fi

# ===================================================================
# Telemetry pipeline (bot -> API -> Postgres)
# ===================================================================
section "Telemetry"

# Latest BotEvent timestamp from API
EVENTS_JSON="$(curl -s --max-time 5 "$BASE_URL/api/events?limit=1")"
LATEST_TS="$(echo "$EVENTS_JSON" | grep -oE '"timestamp":"[^"]+"' | head -1 | cut -d'"' -f4)"

if [ -z "$LATEST_TS" ]; then
  warn "No BotEvent rows found yet (bot may not have emitted any events)"
else
  AGE_SEC="$(($(date -u +%s) - $(date -u -d "$LATEST_TS" +%s 2>/dev/null || echo 0)))"
  if [ "$AGE_SEC" -lt 300 ]; then
    pass "Latest BotEvent is ${AGE_SEC}s old — telemetry pipeline alive"
  elif [ "$AGE_SEC" -lt 3600 ]; then
    warn "Latest BotEvent is ${AGE_SEC}s old — bot may be idle (outside trading window?)"
  else
    fail "Latest BotEvent is ${AGE_SEC}s old — telemetry pipeline may be broken"
  fi
fi

# Today's session record
SESSION_JSON="$(curl -s --max-time 5 "$BASE_URL/api/sessions/today")"
if [ "$SESSION_JSON" = "null" ]; then
  warn "No session record for today (will be created at next 07:00 ET session_open)"
else
  SESSION_MODE="$(echo "$SESSION_JSON" | grep -oE '"tradingMode":"[^"]+"' | head -1 | cut -d'"' -f4)"
  STARTING_EQ="$(echo "$SESSION_JSON" | grep -oE '"startingEquity":[0-9.]+' | head -1 | cut -d':' -f2)"
  pass "Today's session: mode=$SESSION_MODE, starting equity=\$$STARTING_EQ"
fi

# ===================================================================
# Alpaca connectivity (via /api/portfolio/account)
# ===================================================================
section "Alpaca"

ACCT="$(curl -s --max-time 10 "$BASE_URL/api/portfolio/account")"
if echo "$ACCT" | grep -q '"equity"'; then
  MODE="$(echo "$ACCT" | grep -oE '"mode":"[^"]+"' | head -1 | cut -d'"' -f4)"
  EQUITY="$(echo "$ACCT" | grep -oE '"equity":[0-9.]+' | head -1 | cut -d':' -f2)"
  STATUS="$(echo "$ACCT" | grep -oE '"status":"[^"]+"' | head -1 | cut -d'"' -f4)"
  pass "Alpaca account: mode=$MODE, equity=\$$EQUITY, status=$STATUS"

  if [ "$MODE" = "paper" ]; then
    pass "Mode is PAPER — safe for testing"
  else
    warn "Mode is $MODE — REAL MONEY"
  fi
elif echo "$ACCT" | grep -q '"error":"Alpaca credentials missing'; then
  fail "Alpaca credentials missing in .env (ALPACA_API_KEY, ALPACA_SECRET_KEY)"
else
  fail "Could not reach Alpaca: $ACCT"
fi

# Quick live data check
BARS="$(curl -s --max-time 10 "$BASE_URL/api/market/bars?symbol=SPY&timeframe=5Min&limit=5")"
BAR_COUNT="$(echo "$BARS" | grep -oE '"time":' | wc -l | tr -d ' ')"
if [ "$BAR_COUNT" -ge 1 ]; then
  pass "Alpaca data API: returned $BAR_COUNT bar(s) for SPY"
else
  warn "Alpaca data API returned no bars (market closed? IEX feed gap?)"
fi

# ===================================================================
# Disk / logs
# ===================================================================
section "Disk"

if [ -d "$ROOT/bot/logs" ]; then
  LOG_SIZE_MB="$(du -sm "$ROOT/bot/logs" 2>/dev/null | cut -f1)"
  if [ "$LOG_SIZE_MB" -lt 500 ]; then
    pass "bot/logs: ${LOG_SIZE_MB}MB"
  else
    warn "bot/logs: ${LOG_SIZE_MB}MB — consider reviewing log rotation"
  fi
fi

# Free space on /
if command -v df >/dev/null 2>&1; then
  AVAIL_GB="$(df -BG "$ROOT" 2>/dev/null | awk 'NR==2 {gsub("G",""); print $4}')"
  if [ -n "$AVAIL_GB" ]; then
    if [ "$AVAIL_GB" -gt 5 ]; then
      pass "Free space at $ROOT: ${AVAIL_GB}G"
    else
      warn "Low free space at $ROOT: ${AVAIL_GB}G"
    fi
  fi
fi

# ===================================================================
# Summary
# ===================================================================
echo ""
echo -e "${BOLD}== Summary ==${NC}"
echo -e "  ${GREEN}PASS:${NC} $PASS_COUNT"
echo -e "  ${YELLOW}WARN:${NC} $WARN_COUNT"
echo -e "  ${RED}FAIL:${NC} $FAIL_COUNT"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo -e "${RED}${BOLD}Health check FAILED.${NC} See ✗ entries above for details."
  exit 1
elif [ "$WARN_COUNT" -gt 0 ]; then
  echo -e "${YELLOW}Health check OK with warnings.${NC} Review ⚠ entries."
  exit 0
else
  echo -e "${GREEN}${BOLD}All systems green.${NC}"
  exit 0
fi
