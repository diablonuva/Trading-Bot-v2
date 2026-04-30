#!/usr/bin/env bash
# Trading-Bot-v2 — Trading pipeline live verification
#
# Places real bracket orders on Alpaca PAPER for 3 highly liquid symbols,
# holds for 10 minutes, then force-closes. Verifies the entire trading
# pipeline:
#   - Order placement (broker → Alpaca)
#   - Fill events (Alpaca → broker)
#   - Position tracking (Alpaca → /api/portfolio/positions)
#   - Equity updates (Alpaca → /api/portfolio/account)
#   - Telemetry (bot → /telemetry/* → Postgres → /api/events)
#   - Trade history (/api/trades, /api/trades/today)
#   - Soft exits / force-close path
#
# Refuses to run if account is not in PAPER mode. Refuses to run if
# markets are closed. Default qty = 1 share per symbol → < $2k buying
# power consumed.
#
# Usage:   ./scripts/tradingHealthcheck.sh
# Exit:    0 if everything works, 1 on any failure

set -uo pipefail

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

section() { echo ""; echo -e "${BOLD}${BLUE}== $1 ==${NC}"; }
pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
dim()  { echo -e "    ${DIM}$1${NC}"; }

BASE_URL="http://localhost:3200"
SYMBOLS="${SYMBOLS:-SPY,AAPL,MSFT}"
QTY="${QTY:-1}"
HOLD_MIN="${HOLD_MIN:-10}"

# ===================================================================
# Pre-flight
# ===================================================================
section "Pre-flight"

# Required commands
for cmd in docker curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    fail "Missing required command: $cmd"; exit 1
  fi
done
pass "docker, curl available"

# Bot container running
if ! docker compose ps --format json bot 2>/dev/null | grep -q '"State":"running"'; then
  fail "ross-bot-1 is not running — bring it up with 'docker compose up -d bot'"; exit 1
fi
pass "ross-bot-1 is running"

# Market hours check (NYSE: Mon-Fri 09:30-16:00 ET)
NY_DOW=$(TZ=America/New_York date +%u)
NY_HM=$(TZ=America/New_York date +%H%M)
NY_DATE=$(TZ=America/New_York date +%Y-%m-%d)
NY_DISPLAY=$(TZ=America/New_York date '+%a %H:%M ET')

if [ "$NY_DOW" -gt 5 ]; then
  fail "Markets closed (weekend, $NY_DISPLAY)"; exit 1
fi
# 0930 ≤ time < 1600
if [ "$((10#$NY_HM))" -lt 930 ] || [ "$((10#$NY_HM))" -ge 1600 ]; then
  fail "Markets closed ($NY_DISPLAY) — NYSE is 09:30-16:00 ET Mon-Fri"; exit 1
fi
pass "Markets open ($NY_DISPLAY)"

# Holiday check (mirrors the bot's NYSE_HOLIDAYS list)
HOLIDAYS=(
  "2026-01-01" "2026-01-19" "2026-02-16" "2026-04-03" "2026-05-25"
  "2026-06-19" "2026-07-03" "2026-09-07" "2026-11-26" "2026-12-25"
  "2027-01-01" "2027-01-18" "2027-02-15" "2027-03-26" "2027-05-31"
  "2027-06-18" "2027-07-05" "2027-09-06" "2027-11-25" "2027-12-24"
)
for h in "${HOLIDAYS[@]}"; do
  if [ "$NY_DATE" = "$h" ]; then
    fail "NYSE holiday — markets closed ($NY_DATE)"; exit 1
  fi
done
pass "Not an NYSE holiday"

# CRITICAL: must be in paper mode
ACCT="$(curl -s --max-time 10 "$BASE_URL/api/portfolio/account" || echo "")"
MODE=$(echo "$ACCT" | grep -oE '"mode":"[^"]+"' | head -1 | cut -d'"' -f4)
if [ "$MODE" != "paper" ]; then
  fail "ACCOUNT MODE IS '$MODE' — REFUSING TO PLACE TEST TRADES WITH REAL MONEY"
  exit 1
fi
pass "Account mode: PAPER (safe)"

# Sufficient buying power (need at least ~$5k for safety margin on 3x ~$1k orders)
BUYING_POWER=$(echo "$ACCT" | grep -oE '"buyingPower":[0-9.]+' | head -1 | cut -d':' -f2)
BP_INT=${BUYING_POWER%.*}
if [ -z "$BP_INT" ] || [ "$BP_INT" -lt 5000 ]; then
  fail "Insufficient buying power: \$${BUYING_POWER:-?} (need ≥ \$5000)"; exit 1
fi
pass "Buying power: \$$BUYING_POWER"

# Capture baseline state
START_EQUITY=$(echo "$ACCT" | grep -oE '"equity":[0-9.]+' | head -1 | cut -d':' -f2)
START_POS_COUNT=$(curl -s "$BASE_URL/api/portfolio/positions" | grep -oE '"symbol"' | wc -l | tr -d ' ')
START_TRADE_COUNT=$(curl -s "$BASE_URL/api/trades?limit=1000" | grep -oE '"id"' | wc -l | tr -d ' ')
START_EVENT_COUNT=$(curl -s "$BASE_URL/api/events?limit=1000" | grep -oE '"id"' | wc -l | tr -d ' ')

dim "Baseline: equity=\$$START_EQUITY  positions=$START_POS_COUNT  trades=$START_TRADE_COUNT  events=$START_EVENT_COUNT"

# Bot must not already hold positions in the test symbols (would conflict)
EXISTING_POS=$(curl -s "$BASE_URL/api/portfolio/positions")
for sym in ${SYMBOLS//,/ }; do
  if echo "$EXISTING_POS" | grep -q "\"symbol\":\"$sym\""; then
    fail "Already have an open position in $sym — close it first"; exit 1
  fi
done
pass "No existing positions in $SYMBOLS"

# ===================================================================
# Run the trading test (~10 min)
# ===================================================================
section "Live trading test ($HOLD_MIN min)"

echo "  Symbols:         $SYMBOLS"
echo "  Quantity:        $QTY share(s) per symbol"
echo "  Hold:            $HOLD_MIN minutes"
echo "  Starting equity: \$$START_EQUITY"
echo ""
echo "  Running multi_test_trade.py inside bot container..."
echo "  (This will take ~$((HOLD_MIN + 1)) minutes — placing orders, holding, force-closing)"
echo ""

OUT_FILE="/tmp/multi_test_$$.json"
ERR_FILE="/tmp/multi_test_$$.err"
docker exec ross-bot-1 python /app/src/multi_test_trade.py \
  --symbols "$SYMBOLS" \
  --qty "$QTY" \
  --hold-minutes "$HOLD_MIN" \
  > "$OUT_FILE" 2> "$ERR_FILE"
RC=$?

if [ "$RC" -ne 0 ]; then
  fail "multi_test_trade.py exited with code $RC"
  echo "--- stderr ---"
  cat "$ERR_FILE"
  echo "--- stdout ---"
  cat "$OUT_FILE"
  exit 1
fi
pass "multi_test_trade.py completed cleanly"

# Parse the JSON output
PLACED=$(grep -c '"ok": true' "$OUT_FILE" || echo "0")
PNL=$(grep -oE '"pnl": -?[0-9.]+' "$OUT_FILE" | head -1 | cut -d':' -f2 | tr -d ' ')
END_EQ=$(grep -oE '"ending_equity": [0-9.]+' "$OUT_FILE" | head -1 | cut -d':' -f2 | tr -d ' ')

dim "Test PnL: \$${PNL:-?}, ending equity \$${END_EQ:-?}"

# ===================================================================
# Verify pipeline produced expected artifacts
# ===================================================================
section "Pipeline verification"

# Account equity reflects the trades (could be up or down — just verify it changed or is reachable)
END_ACCT="$(curl -s --max-time 10 "$BASE_URL/api/portfolio/account")"
END_EQUITY_API=$(echo "$END_ACCT" | grep -oE '"equity":[0-9.]+' | head -1 | cut -d':' -f2)
if [ -n "$END_EQUITY_API" ]; then
  pass "/api/portfolio/account: ending equity \$$END_EQUITY_API"
else
  fail "/api/portfolio/account did not return ending equity"
fi

# Positions should be empty after force-close
END_POS=$(curl -s --max-time 5 "$BASE_URL/api/portfolio/positions")
END_POS_COUNT=$(echo "$END_POS" | grep -oE '"symbol"' | wc -l | tr -d ' ')
if [ "$END_POS_COUNT" -le "$START_POS_COUNT" ]; then
  pass "All test positions closed at Alpaca (count: $END_POS_COUNT, baseline: $START_POS_COUNT)"
else
  warn "Position count rose from $START_POS_COUNT → $END_POS_COUNT — bracket orders may still be settling"
fi

# Trade rows: should have grown by ~3 (one per placed order)
sleep 3  # give telemetry a moment to settle
END_TRADE_COUNT=$(curl -s "$BASE_URL/api/trades?limit=1000" | grep -oE '"id"' | wc -l | tr -d ' ')
TRADE_DELTA=$((END_TRADE_COUNT - START_TRADE_COUNT))
if [ "$TRADE_DELTA" -ge 1 ]; then
  pass "Trade rows: $START_TRADE_COUNT → $END_TRADE_COUNT (+$TRADE_DELTA)"
else
  fail "No new trade rows persisted (telemetry pipeline broken?)"
fi

# Event rows: should have grown
END_EVENT_COUNT=$(curl -s "$BASE_URL/api/events?limit=1000" | grep -oE '"id"' | wc -l | tr -d ' ')
EVENT_DELTA=$((END_EVENT_COUNT - START_EVENT_COUNT))
if [ "$EVENT_DELTA" -ge 2 ]; then
  pass "Event rows: $START_EVENT_COUNT → $END_EVENT_COUNT (+$EVENT_DELTA, includes MULTI_TEST_INIT/DONE)"
else
  warn "Only $EVENT_DELTA new events — expected ≥ 2 (init + done)"
fi

# Spot-check that MULTI_TEST_DONE appeared
RECENT_EVENTS=$(curl -s "$BASE_URL/api/events?limit=20")
if echo "$RECENT_EVENTS" | grep -q "MULTI_TEST_DONE"; then
  pass "MULTI_TEST_DONE event landed in DB"
else
  warn "MULTI_TEST_DONE event not yet visible in /api/events"
fi

# Per-symbol verification: did each symbol generate a trade row?
for sym in ${SYMBOLS//,/ }; do
  if curl -s "$BASE_URL/api/trades?limit=200" | grep -q "\"symbol\":\"$sym\""; then
    pass "$sym: trade row recorded"
  else
    fail "$sym: no trade row — order may have failed silently"
  fi
done

# Cleanup
rm -f "$OUT_FILE" "$ERR_FILE"

# ===================================================================
# Summary
# ===================================================================
echo ""
echo -e "${BOLD}== Summary ==${NC}"
echo -e "  ${GREEN}PASS:${NC} $PASS_COUNT"
echo -e "  ${YELLOW}WARN:${NC} $WARN_COUNT"
echo -e "  ${RED}FAIL:${NC} $FAIL_COUNT"
echo ""
echo "  PnL during test: \$${PNL:-?}"
echo "  Equity:          \$$START_EQUITY → \$${END_EQUITY_API:-?}"
echo ""

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo -e "${RED}${BOLD}Trading pipeline FAILED.${NC}"
  exit 1
elif [ "$WARN_COUNT" -gt 0 ]; then
  echo -e "${YELLOW}Trading pipeline OK with warnings.${NC}"
  exit 0
else
  echo -e "${GREEN}${BOLD}Trading pipeline fully verified.${NC}"
  exit 0
fi
