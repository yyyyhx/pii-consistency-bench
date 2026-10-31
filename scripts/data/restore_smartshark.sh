#!/usr/bin/env bash
# Restore the identity-linking subset of SmartSHARK 2.1 Small into a local MongoDB (Docker).
#
#   scripts/data/restore_smartshark.sh start     # create/start container smartshark-mongo (idempotent)
#   scripts/data/restore_smartshark.sh probe     # dry run: list the archive's namespaces (confirms DB name)
#   scripts/data/restore_smartshark.sh restore   # drop + restore only $COLLECTIONS, then print counts
#   scripts/data/restore_smartshark.sh counts    # per-collection document counts and sizes
#   scripts/data/restore_smartshark.sh stop      # stop the container (data stays in <data>/mongo-data)
#
# probe/restore refuse to run until fetch_smartshark.sh has verified the download (<archive>.md5ok).
# Env overrides: PIIBENCH_DATA_DIR (default: <repo>/../dataset), SMARTSHARK_DB (default smartshark_2_1),
# SMARTSHARK_CONTAINER, SMARTSHARK_PORT (default 27017), SMARTSHARK_IMAGE.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DATA_DIR="${PIIBENCH_DATA_DIR:-$(cd "$REPO/.." && pwd)/dataset}"
ARCHIVE="$DATA_DIR/smartshark_2_1_small.agz"
DB_DIR="$DATA_DIR/mongo-data"
LOG="$DATA_DIR/_logs/smartshark_restore.log"
# Archive prelude: db smartshark_2_1, dumped by mongod 4.0.10 / mongodump r4.0.16; 4.4 restores it natively.
DB="${SMARTSHARK_DB:-smartshark_2_1}"
CONTAINER="${SMARTSHARK_CONTAINER:-smartshark-mongo}"
PORT="${SMARTSHARK_PORT:-27017}"
IMAGE="${SMARTSHARK_IMAGE:-mongo:4.4.29@sha256:52c42cbab240b3c5b1748582cc13ef46d521ddacae002bbbda645cebed270ec0}"
# Collections carrying person ids (people._id) plus the system/project docs they hang off.
# people and identity are withheld from the public dumps (arXiv:2102.11540 §I); `counts` shows them as absent.
COLLECTIONS=(
  people identity project
  vcs_system commit tag
  issue_system issue issue_comment event
  pull_request_system pull_request pull_request_comment pull_request_event pull_request_commit
  pull_request_review pull_request_review_comment
  mailing_list message
  travis_build travis_job
)

mkdir -p "$(dirname "$LOG")"

require_archive() {
  if [[ ! -f "$ARCHIVE" || ! -f "$ARCHIVE.md5ok" ]]; then
    echo "refusing: $ARCHIVE and $ARCHIVE.md5ok must both exist (check: fetch_smartshark.sh status)" >&2
    exit 1
  fi
}

mongo_eval() {
  docker exec "$CONTAINER" mongo --quiet "$DB" --eval "$1"
}

start() {
  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    docker start "$CONTAINER" >/dev/null
  else
    mkdir -p "$DB_DIR"
    # mongod runs as root: on Docker Desktop (macOS) bind mounts, the image entrypoint's non-root
    # mongodb user gets EPERM reopening an existing WiredTiger.wt, so restarts would fail.
    docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:27017" -v "$DB_DIR:/data/db" \
      --entrypoint mongod "$IMAGE" --bind_ip_all >/dev/null
  fi
  for _ in $(seq 1 60); do
    if mongo_eval 'db.adminCommand({ping: 1}).ok' >/dev/null 2>&1; then
      echo "mongod up: $CONTAINER on 127.0.0.1:$PORT, data in $DB_DIR"
      return
    fi
    [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" == true ]] || break
    sleep 1
  done
  echo "mongod not ready; see: docker logs $CONTAINER" >&2
  exit 1
}

# Throwaway tools container on mongod's network; the verified archive is mounted read-only.
tools() {
  docker run --rm --network "container:$CONTAINER" -v "$ARCHIVE:/archive.agz:ro" "$IMAGE" "$@"
}

counts() {
  local names
  names="$(printf '"%s",' "${COLLECTIONS[@]}")"
  mongo_eval "
    var have = db.getCollectionNames(), mb = function (b) { return (b / 1048576).toFixed(0) + ' MB'; };
    print('collection'.padEnd(30) + 'docs'.padStart(12) + 'data'.padStart(12) + 'disk'.padStart(12));
    [${names%,}].forEach(function (c) {
      if (have.indexOf(c) < 0) { print(c.padEnd(30) + 'absent'.padStart(12)); return; }
      var s = db[c].stats();
      print(c.padEnd(30) + String(s.count).padStart(12) + mb(s.size).padStart(12) +
            mb(s.storageSize + s.totalIndexSize).padStart(12));
    });
    var t = db.stats();
    print('total ' + db.getName() + ': data ' + mb(t.dataSize) + ', disk ' + mb(t.storageSize + t.indexSize));"
}

case "${1:-}" in
  start)
    start
    ;;
  probe)
    require_archive
    start
    tools mongorestore --gzip --archive=/archive.agz --dryRun -v 2>&1 | tee -a "$LOG"
    ;;
  restore)
    require_archive
    start
    ns=()
    for c in "${COLLECTIONS[@]}"; do ns+=("--nsInclude=$DB.$c"); done
    echo "=== $(date '+%F %T') restore $ARCHIVE -> $DB (${#COLLECTIONS[@]} collections)" >> "$LOG"
    tools mongorestore --gzip --archive=/archive.agz --drop "${ns[@]}" 2>&1 | tee -a "$LOG"
    echo "=== $(date '+%F %T') restore done" >> "$LOG"
    counts | tee -a "$LOG"
    ;;
  counts)
    counts
    ;;
  stop)
    if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
      docker stop "$CONTAINER" >/dev/null && echo "stopped $CONTAINER (data kept in $DB_DIR)"
    else
      echo "no container $CONTAINER"
    fi
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac
