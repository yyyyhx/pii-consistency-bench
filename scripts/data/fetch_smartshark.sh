#!/usr/bin/env bash
# Fetch SmartSHARK 2.1 Small (doi:10.25625/PHV2VX, file doi:10.25625/PHV2VX/TRSQDS).
#
#   scripts/data/fetch_smartshark.sh probe    # ~3 min endpoint diagnostics (bytes discarded)
#   scripts/data/fetch_smartshark.sh start    # start/resume in background; survives closing the terminal
#   scripts/data/fetch_smartshark.sh status   # progress, speed, ETA, holder processes
#   scripts/data/fetch_smartshark.sh stop     # checkpoint and stop
#   scripts/data/fetch_smartshark.sh verify   # recompute MD5 of the finished file
#
# Env overrides: PIIBENCH_DATA_DIR (default: <repo>/../dataset), SMARTSHARK_URL,
# SMARTSHARK_CONNECTIONS (default 6), PYTHON (default python3).
# Size and MD5 come from the Dataverse API:
#   https://data.goettingen-research-online.de/api/datasets/:persistentId/?persistentId=doi:10.25625/PHV2VX
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DATA_DIR="${PIIBENCH_DATA_DIR:-$(cd "$REPO/.." && pwd)/dataset}"
NAME=smartshark_2_1_small.agz
OUT="$DATA_DIR/$NAME"
LOG_DIR="$DATA_DIR/_logs"
PID_FILE="$LOG_DIR/smartshark_download.pid"
URL_API="https://data.goettingen-research-online.de/api/access/datafile/32457?gbrecs=true"
URL_ALT="https://data.goettingen-research-online.de/download/smartshark_2_1_small.agz"
SIZE=11968836074
MD5=8dca79068b308eb3c80ace0fc4771c19
# Probe 2026-10-23: endpoint B (static file) 223 KB/s on 1 conn, 891 KB/s on 8; endpoint A stalls >4 conns.
URL="${SMARTSHARK_URL:-$URL_ALT}"
CONNECTIONS="${SMARTSHARK_CONNECTIONS:-8}"
PY="${PYTHON:-python3}"
DL="$HERE/resumable_download.py"
# New session when possible (fails harmlessly for a job-control group leader; nohup still applies).
DETACH='import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])'

mkdir -p "$LOG_DIR"

running_pid() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    cat "$PID_FILE"
  fi
}

case "${1:-}" in
  probe)
    "$PY" "$DL" probe --url "$URL_API" --url "$URL_ALT" --size "$SIZE" \
      --seconds 20 --connections 1 4 8 --log "$LOG_DIR/smartshark_probe.log"
    ;;
  start)
    if [[ -n "$(running_pid)" ]]; then
      echo "already running (pid $(running_pid)); use: $0 status"
      exit 0
    fi
    # setsid: own session/process group, so closing the launching terminal or tool session
    # cannot signal it; nohup additionally ignores SIGHUP.
    "$PY" -c "$DETACH" \
      nohup "$PY" "$DL" download --url "$URL" --out "$OUT" --size "$SIZE" --md5 "$MD5" \
      --magic 1f8b --connections "$CONNECTIONS" --chunk-mb 64 \
      --log "$LOG_DIR/smartshark_download.log" >> "$LOG_DIR/smartshark_download.stdout" 2>&1 < /dev/null &
    pid=$!
    echo "$pid" > "$PID_FILE"
    # Keep the Mac awake while the downloader runs (released automatically when it exits).
    "$PY" -c "$DETACH" \
      caffeinate -i -s -w "$pid" >/dev/null 2>&1 < /dev/null &
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
      echo "started pid $pid -> $OUT"
      echo "log: $LOG_DIR/smartshark_download.log"
    else
      echo "downloader exited immediately; last log lines:"
      tail -n 20 "$LOG_DIR/smartshark_download.log" 2>/dev/null || tail -n 20 "$LOG_DIR/smartshark_download.stdout"
      exit 1
    fi
    ;;
  status)
    "$PY" "$DL" status --out "$OUT"
    if [[ -n "$(running_pid)" ]]; then echo "downloader running: pid $(running_pid)"; else echo "downloader not running"; fi
    echo "--- last log lines"
    tail -n 5 "$LOG_DIR/smartshark_download.log" 2>/dev/null || true
    ;;
  stop)
    pid="$(running_pid)"
    if [[ -z "$pid" ]]; then echo "not running"; exit 0; fi
    kill -INT "$pid"
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && echo "still running after 30s (pid $pid)" || echo "stopped; state checkpointed"
    ;;
  verify)
    "$PY" "$DL" verify --file "$OUT" --md5 "$MD5"
    ;;
  *)
    sed -n '2,13p' "$0"
    exit 1
    ;;
esac
