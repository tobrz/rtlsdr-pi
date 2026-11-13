#!/bin/sh
# Retention for Kismet survey logs. A continuous Kismet run writes one ever-
# growing .kismet (SQLite) file, so this: (1) deletes logs older than
# RETAIN_DAYS, (2) caps the directory at MAX_MB by removing the oldest files
# first — never the newest, which Kismet still has open — and (3) restarts
# Kismet so each day rolls into its own dated file. Driven by a daily timer.
set -eu
DIR="${KISMET_LOG_DIR:-/var/lib/rtlsdr-pi/kismet}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
MAX_MB="${MAX_MB:-3000}"
[ -d "$DIR" ] || exit 0

# 1. Age: drop logs (and their -journal sidecars) older than RETAIN_DAYS.
find "$DIR" -maxdepth 1 -type f -name '*.kismet*' -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true

# 2. Size cap: remove the oldest .kismet first until under MAX_MB. `ls -1t`
#    is newest-first; `tail -n +2` protects the newest (the active file).
while [ "$(du -sm "$DIR" 2>/dev/null | cut -f1)" -gt "$MAX_MB" ]; do
  oldest="$(ls -1t "$DIR"/*.kismet 2>/dev/null | tail -n +2 | tail -1)"
  [ -n "$oldest" ] || break
  rm -f "$oldest" "${oldest}-journal" 2>/dev/null || true
done

# 3. Roll: restart Kismet so a fresh dated log starts (a ~1 s capture gap).
systemctl try-restart kismet.service 2>/dev/null || true
