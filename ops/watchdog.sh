#!/usr/bin/env bash
# Mirror Engine host watchdog.
#
# Runs from cron ON THE EC2 HOST (outside Docker), so it keeps working even when
# the backend process hangs or is OOM-killed — the exact failure that once took
# login down silently, because the in-process monitor died with the process.
#
# Each run: hit the backend health endpoint. If it's not 200, restart the
# backend container and re-check. Telegram is notified only on state TRANSITIONS
# (went down / recovered) so it never spams; a service that stays down re-alerts
# at most once per RENOTIFY_SEC.
#
# Telegram creds are read from backend/.env (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)
# — no secrets live in this script or in cron.
set -u

REPO="${REPO:-/home/ubuntu/Mirror_Engine}"
ENV_FILE="$REPO/backend/.env"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"
CONTAINER="${CONTAINER:-copytrade_backend}"
STATE_FILE="${STATE_FILE:-/tmp/me_watchdog.state}"
RENOTIFY_SEC="${RENOTIFY_SEC:-1800}"   # re-alert a still-down service at most every 30 min

_val() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'\r'; }
BOT="$(_val TELEGRAM_BOT_TOKEN)"
CHAT="$(_val TELEGRAM_CHAT_ID)"

tg() {
  [ -n "$BOT" ] && [ -n "$CHAT" ] || return 0
  curl -s -m 10 -o /dev/null \
    "https://api.telegram.org/bot${BOT}/sendMessage" \
    --data-urlencode "chat_id=${CHAT}" \
    --data-urlencode "parse_mode=HTML" \
    --data-urlencode "disable_web_page_preview=true" \
    --data-urlencode "text=$1" || true
}

health() { curl -s -m 10 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000; }

now="$(date +%s)"
host="$(hostname)"

# Previous state: "OK <epoch> <epoch>" | "DOWN <since> <last_notify>"
prev="OK"; since="$now"; last_notify=0
if [ -f "$STATE_FILE" ]; then
  read -r prev since last_notify < "$STATE_FILE" 2>/dev/null || true
  prev="${prev:-OK}"; since="${since:-$now}"; last_notify="${last_notify:-0}"
fi

code="$(health)"

# --- healthy ---
if [ "$code" = "200" ]; then
  if [ "$prev" = "DOWN" ]; then
    dur=$(( now - since )); [ "$dur" -lt 0 ] && dur=0
    tg "✅ <b>Mirror Engine recovered</b>
Backend healthy again on ${host} (was down ~$(( dur/60 ))m)."
  fi
  echo "OK $now $now" > "$STATE_FILE"
  exit 0
fi

# --- unhealthy: attempt self-heal, then re-check ---
sudo -n docker restart "$CONTAINER" >/dev/null 2>&1; restart_rc=$?
sleep 12
code2="$(health)"

if [ "$code2" = "200" ]; then
  if [ "$prev" = "DOWN" ]; then
    tg "✅ <b>Mirror Engine recovered</b>
Auto-restart of <code>${CONTAINER}</code> succeeded — backend healthy again."
  else
    tg "⚠️ <b>Mirror Engine self-healed</b>
Backend health failed (HTTP ${code}) on ${host}; auto-restart of <code>${CONTAINER}</code> fixed it (now HTTP 200)."
  fi
  echo "OK $now $now" > "$STATE_FILE"
  exit 0
fi

# --- still down after restart ---
[ "$prev" != "DOWN" ] && since="$now"
if [ "$prev" != "DOWN" ] || [ $(( now - last_notify )) -ge "$RENOTIFY_SEC" ]; then
  tg "🚨 <b>Mirror Engine DOWN</b>
Backend health failed (HTTP ${code}) and auto-restart did NOT recover it (now HTTP ${code2}, restart rc=${restart_rc}) on ${host}.
Manual intervention needed."
  last_notify="$now"
fi
echo "DOWN $since $last_notify" > "$STATE_FILE"
exit 1
