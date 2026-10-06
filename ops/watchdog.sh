#!/usr/bin/env bash
# Mirror Engine host watchdog (v2). Runs from cron on the EC2 host (outside
# Docker), so it keeps working even when the backend hangs or is OOM-killed.
#
# Design goals (v2): don't be trigger-happy, and don't spam.
#  * A failure is CONFIRMED with a second check before any restart, so a
#    momentary stall doesn't cause a needless restart.
#  * After a restart we POLL for up to GRACE_SEC for the app to boot, instead of
#    a single short check (which used to false-alarm "did not recover").
#  * Telegram is sent ONLY when a human needs to care:
#      - genuine outage: a restart did NOT recover the backend within GRACE_SEC
#        (re-alerted at most every RENOTIFY_SEC),
#      - recovery from such an outage,
#      - flapping: if it has auto-restarted a lot, ONE summary per hour (the
#        usual cause is memory pressure — resize the instance).
#    A routine self-heal is LOGGED, never messaged — that was the old spam.
#
# Telegram creds come from backend/.env; nothing secret lives here or in cron.
set -u

REPO="${REPO:-/home/ubuntu/Mirror_Engine}"
ENV_FILE="${ENV_FILE:-$REPO/backend/.env}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"
CONTAINER="${CONTAINER:-copytrade_backend}"
STATE_FILE="${STATE_FILE:-/tmp/me_watchdog.state}"
FLAP_FILE="${FLAP_FILE:-/tmp/me_watchdog.flaps}"
RENOTIFY_SEC="${RENOTIFY_SEC:-1800}"      # re-alert a still-down backend at most every 30m
GRACE_SEC="${GRACE_SEC:-60}"              # wait this long for a restart to take effect
CONFIRM_SEC="${CONFIRM_SEC:-8}"           # gap before re-checking, to confirm a real failure
FLAP_WINDOW="${FLAP_WINDOW:-3600}"        # count restarts within this window (1h)
FLAP_THRESHOLD="${FLAP_THRESHOLD:-3}"     # >= this many restarts/window -> one summary
FLAP_NOTIFY_SEC="${FLAP_NOTIFY_SEC:-3600}"

# Disk. A full root filesystem is the one failure that /health cannot see and a
# restart cannot fix: the process answers 200 while being unable to write a
# single byte. 2026-10-05 00:00 the disk hit 100% (a 5.5 GB uncapped Docker log
# plus 8 GB of stale image layers); copying and logins were dead for 31 hours and
# nothing alerted, because the health check stayed green the whole time.
DISK_PATH="${DISK_PATH:-/}"
DISK_WARN_PCT="${DISK_WARN_PCT:-85}"      # alert while there is still room to act
DISK_NOTIFY_SEC="${DISK_NOTIFY_SEC:-21600}"   # re-alert at most every 6h
DISK_STATE="${DISK_STATE:-/tmp/me_watchdog.disk}"

_val() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'\r'; }
BOT="$(_val TELEGRAM_BOT_TOKEN)"; CHAT="$(_val TELEGRAM_CHAT_ID)"

log() { echo "$(date '+%F %T') $*"; }
tg() {
  [ -n "$BOT" ] && [ -n "$CHAT" ] || return 0
  curl -s -m 10 -o /dev/null "https://api.telegram.org/bot${BOT}/sendMessage" \
    --data-urlencode "chat_id=${CHAT}" --data-urlencode "parse_mode=HTML" \
    --data-urlencode "disable_web_page_preview=true" --data-urlencode "text=$1" || true
}
# Single clean status code; caller defaults empty to 000 (no double-echo -> no "000000").
health() { curl -s -m 8 -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null; }

disk_pct() { df -P "$DISK_PATH" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}'; }
disk_top() { du -xh --max-depth=2 / 2>/dev/null | sort -rh | sed -n '2,4p' | tr '
' ' '; }

# Alerts, never restarts: restarting into a full disk changes nothing. Own state
# file so the existing 4-field state line keeps its format.
check_disk() {
  local pct last
  pct="$(disk_pct)"
  case "$pct" in ''|*[!0-9]*) return 0;; esac
  last=0; [ -f "$DISK_STATE" ] && read -r last < "$DISK_STATE" 2>/dev/null
  case "$last" in ''|*[!0-9]*) last=0;; esac

  if [ "$pct" -ge "$DISK_WARN_PCT" ]; then
    log "disk ${pct}% >= ${DISK_WARN_PCT}% on $DISK_PATH"
    if [ $(( now - last )) -ge "$DISK_NOTIFY_SEC" ]; then
      tg "$(printf '🟠 <b>Mirror Engine: disk %s%% full</b>
Host %s, %s.
At 100%% the backend keeps answering /health but cannot write, so copying and logins stop silently — a restart does NOT fix it.
Biggest: %s
Free space with <code>docker system prune -af</code>.' "$pct" "$host" "$DISK_PATH" "$(disk_top)")"
      echo "$now" > "$DISK_STATE"
    fi
  elif [ "$last" -ne 0 ]; then
    tg "$(printf '✅ <b>Mirror Engine: disk back to %s%%</b> on %s.' "$pct" "$host")"
    : > "$DISK_STATE"
  fi
}

now="$(date +%s)"; host="$(hostname)"

# Checked every run, before the health branch returns early on a healthy backend —
# the disk can be full while /health is still 200.
check_disk

# state line: STATUS SINCE LAST_NOTIFY LAST_FLAP
STATUS=OK; SINCE=0; LAST_NOTIFY=0; LAST_FLAP=0
if [ -f "$STATE_FILE" ]; then
  read -r STATUS SINCE LAST_NOTIFY LAST_FLAP < "$STATE_FILE" 2>/dev/null || true
  STATUS="${STATUS:-OK}"; SINCE="${SINCE:-0}"; LAST_NOTIFY="${LAST_NOTIFY:-0}"; LAST_FLAP="${LAST_FLAP:-0}"
fi

code="$(health)"; [ -z "$code" ] && code=000
# Confirm a failure is real before acting (filters transient stalls).
if [ "$code" != "200" ]; then
  sleep "$CONFIRM_SEC"
  code="$(health)"; [ -z "$code" ] && code=000
fi

# ---- healthy ----
if [ "$code" = "200" ]; then
  if [ "$STATUS" = "DOWN" ]; then
    dur=$(( now - SINCE )); [ "$dur" -lt 0 ] && dur=0
    tg "$(printf '✅ <b>Mirror Engine recovered</b>\nBackend healthy again on %s (was down ~%dm).' "$host" "$(( dur/60 ))")"
    log "recovered from outage (${dur}s)"
  fi
  echo "OK 0 $LAST_NOTIFY $LAST_FLAP" > "$STATE_FILE"
  exit 0
fi

# ---- confirmed unhealthy -> restart, then wait for boot ----
log "health=$code confirmed down -> restarting $CONTAINER"
sudo -n docker restart "$CONTAINER" >/dev/null 2>&1; rc=$?
code2=000; waited=0
while [ "$waited" -lt "$GRACE_SEC" ]; do
  sleep 5; waited=$(( waited + 5 ))
  code2="$(health)"; [ -z "$code2" ] && code2=000
  [ "$code2" = "200" ] && break
done

# ---- restart recovered it (self-heal) ----
if [ "$code2" = "200" ]; then
  echo "$now" >> "$FLAP_FILE"
  tmpf="$(mktemp 2>/dev/null || echo /tmp/me_wd_flap.$$)"
  awk -v cut="$(( now - FLAP_WINDOW ))" '$1>=cut' "$FLAP_FILE" 2>/dev/null > "$tmpf" && mv "$tmpf" "$FLAP_FILE"
  flaps="$(wc -l < "$FLAP_FILE" 2>/dev/null | tr -d ' ')"; [ -z "$flaps" ] && flaps=1
  log "self-healed in ${waited}s (restarts in last hour: $flaps)"

  if [ "$STATUS" = "DOWN" ]; then
    # was a genuine outage that we'd alerted — close it out.
    tg "$(printf '✅ <b>Mirror Engine recovered</b>\nAuto-restart brought the backend back on %s.' "$host")"
  elif [ "$flaps" -ge "$FLAP_THRESHOLD" ] && [ $(( now - LAST_FLAP )) -ge "$FLAP_NOTIFY_SEC" ]; then
    # routine self-heal, but it's happening a lot -> ONE actionable summary/hour.
    tg "$(printf '⚠️ <b>Mirror Engine unstable</b>\nBackend auto-restarted %s times in the last hour on %s and self-recovered each time. Usual cause is memory pressure — consider resizing the instance. It is up right now; no immediate action needed.' "$flaps" "$host")"
    LAST_FLAP="$now"
  fi
  echo "OK 0 $LAST_NOTIFY $LAST_FLAP" > "$STATE_FILE"
  exit 0
fi

# ---- restart did NOT recover within grace -> genuine outage ----
[ "$STATUS" != "DOWN" ] && SINCE="$now"
if [ "$STATUS" != "DOWN" ] || [ $(( now - LAST_NOTIFY )) -ge "$RENOTIFY_SEC" ]; then
  tg "$(printf '🚨 <b>Mirror Engine DOWN</b>\nBackend failed health (HTTP %s) and auto-restart did NOT recover it within %ss (HTTP %s, rc=%s) on %s.\nManual intervention needed.' "$code" "$GRACE_SEC" "$code2" "$rc" "$host")"
  LAST_NOTIFY="$now"
  log "GENUINE OUTAGE alert sent (code=$code code2=$code2 rc=$rc)"
fi
echo "DOWN $SINCE $LAST_NOTIFY $LAST_FLAP" > "$STATE_FILE"
exit 1
