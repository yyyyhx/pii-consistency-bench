#!/usr/bin/env python3
"""Resumable, parallel, verified HTTP range downloader (Python stdlib only).

Subcommands
-----------
probe     Inspect an endpoint: redirects, headers, range support, and
          single- vs multi-connection throughput (bytes are discarded).
download  Download into ``<out>.part`` using parallel byte-range requests.
          Progress is checkpointed to ``<out>.part.state.json`` so the job can
          be interrupted and resumed at any time. On completion the size and
          MD5 are verified and the file is atomically renamed to ``<out>``.
status    Print progress of an in-flight or interrupted download.
verify    Compute the MD5 of a finished file and compare with the expected one.

Safety rules
------------
* Never truncates or overwrites downloaded bytes.
* Refuses to run while another process has the target file open.
* A sequential prefix left by curl/a browser is only adopted after its tail and
  random windows are byte-compared against the server.
* Every range response must be ``206`` with an exact ``Content-Range``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

USER_AGENT = "piibench-downloader/1.0 (research dataset fetch)"
BLOCK = 1 << 20  # 1 MiB read size
MiB = 1 << 20


# ----------------------------------------------------------------------------
# helpers


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


class Logger:
    def __init__(self, path: str | None):
        self.path = path
        self.lock = threading.Lock()
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = f"[{now()}] {msg}"
        with self.lock:
            print(line, flush=True)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} TB"


def hms(seconds: float) -> str:
    if seconds == float("inf") or seconds != seconds:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h{m:02d}m{s:02d}s"


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ssl.create_default_context()


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))


class RangeError(Exception):
    """Server answered a range request with something we must not accept."""


class FatalHTTPError(Exception):
    """Non-retryable HTTP status (e.g. 403/404/410/416)."""


def open_range(opener, url: str, start: int, end: int, timeout: float, expected_total: int | None):
    req = urllib.request.Request(
        url,
        headers={
            "Range": f"bytes={start}-{end}",
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "identity",
        },
    )
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404, 410, 416):
            raise FatalHTTPError(f"HTTP {exc.code} for bytes={start}-{end}") from exc
        raise
    status = resp.status
    content_range = resp.headers.get("Content-Range", "")
    if status != 206:
        resp.close()
        raise RangeError(f"expected 206 for bytes={start}-{end}, got {status} (Content-Range={content_range!r})")
    want_prefix = f"bytes {start}-{end}/"
    if not content_range.startswith(want_prefix):
        resp.close()
        raise RangeError(f"Content-Range mismatch: want {want_prefix}*, got {content_range!r}")
    total = content_range[len(want_prefix):]
    if expected_total is not None and total != "*" and int(total) != expected_total:
        resp.close()
        raise RangeError(f"remote size {total} != expected {expected_total}")
    return resp


def other_open_handles(path: str) -> list[str]:
    if not os.path.exists(path) or shutil.which("lsof") is None:
        return []
    try:
        out = subprocess.run(["lsof", "-F", "pc", "--", path], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    procs, pid = [], None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c") and pid and int(pid) != os.getpid():
            procs.append(f"{line[1:]}(pid {pid})")
    return procs


def md5_file(path: str, log: Logger | None = None) -> str:
    h = hashlib.md5()
    size = os.path.getsize(path)
    done, t0, last = 0, time.time(), 0.0
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(8 * MiB)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if log and time.time() - last > 15:
                last = time.time()
                log(f"md5 progress {done / size * 100:5.1f}% ({human(done)} / {human(size)})")
    if log:
        log(f"md5 computed in {time.time() - t0:.0f}s")
    return h.hexdigest()


# ----------------------------------------------------------------------------
# probe


def cmd_probe(args) -> int:
    log = Logger(args.log)
    opener = build_opener()
    for url in args.url:
        log(f"=== PROBE {url}")
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-0", "User-Agent": USER_AGENT})
            t0 = time.time()
            resp = opener.open(req, timeout=args.timeout)
            ttfb = time.time() - t0
            log(f"status={resp.status} final_url={resp.geturl()} ttfb={ttfb:.2f}s")
            for key in ("Content-Range", "Content-Length", "Accept-Ranges", "Content-Type",
                        "Content-Disposition", "ETag", "Last-Modified", "Server", "Via", "Connection"):
                if resp.headers.get(key) is not None:
                    log(f"  {key}: {resp.headers.get(key)}")
            resp.read()
            resp.close()
        except Exception as exc:  # noqa: BLE001 - diagnostics
            log(f"  initial range request FAILED: {type(exc).__name__}: {exc}")
            continue

        total = args.size
        if total:
            try:
                r = open_range(opener, url, total - 64, total - 1, args.timeout, total)
                tail = r.read()
                r.close()
                log(f"  tail range OK: 206, {len(tail)} bytes, total size matches {total}")
            except Exception as exc:  # noqa: BLE001
                log(f"  tail range FAILED: {type(exc).__name__}: {exc}")

        window = min(1024 * MiB, (total // 2) if total else 1024 * MiB)

        def measure(offset: int, seconds: float, out: dict, key: int) -> None:
            got, err = 0, None
            t_start = time.time()
            try:
                r = open_range(opener, url, offset, offset + window - 1, args.timeout, total)
                while time.time() - t_start < seconds:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    got += len(buf)
                r.close()
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
            out[key] = (got, time.time() - t_start, err)

        span = (total or 10 * 1024 * MiB) - window
        for nconn in args.connections:
            results: dict = {}
            threads = []
            for i in range(nconn):
                off = random.randrange(0, max(span, 1))
                th = threading.Thread(target=measure, args=(off, args.seconds, results, i), daemon=True)
                threads.append(th)
                th.start()
            for th in threads:
                th.join(args.seconds + args.timeout + 5)
            agg = sum(v[0] for v in results.values())
            per = [f"{v[0] / max(v[1], 1e-9) / 1000:.0f}" for v in results.values()]
            errs = [v[2] for v in results.values() if v[2]]
            log(f"  throughput conns={nconn}: aggregate={agg / args.seconds / 1000:.0f} KB/s "
                f"per-conn KB/s={per} errors={errs or 'none'}")
    return 0


# ----------------------------------------------------------------------------
# download


class State:
    def __init__(self, path: str, data: dict):
        self.path = path
        self.data = data
        self.lock = threading.Lock()

    @classmethod
    def new(cls, path, url, size, md5, chunk_size, prefix: int) -> "State":
        n = (size + chunk_size - 1) // chunk_size
        done = []
        for i in range(n):
            start = i * chunk_size
            length = min(chunk_size, size - start)
            done.append(max(0, min(length, prefix - start)))
        return cls(path, {"version": 1, "url": url, "size": size, "md5": md5,
                          "chunk_size": chunk_size, "done": done, "created": now()})

    @classmethod
    def load(cls, path) -> "State":
        with open(path, encoding="utf-8") as fh:
            return cls(path, json.load(fh))

    def chunk_bounds(self, i: int) -> tuple[int, int]:
        cs, size = self.data["chunk_size"], self.data["size"]
        start = i * cs
        return start, min(start + cs, size) - 1

    def chunk_len(self, i: int) -> int:
        s, e = self.chunk_bounds(i)
        return e - s + 1

    def completed_bytes(self) -> int:
        return sum(self.data["done"])

    def pending(self) -> list[int]:
        return [i for i, d in enumerate(self.data["done"]) if d < self.chunk_len(i)]

    def save(self) -> None:
        with self.lock:
            self.data["updated"] = now()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)


def spot_check_prefix(opener, url, path, prefix, size, timeout, log, windows=4) -> bool:
    """Byte-compare the tail and random windows of an adopted prefix with the server."""
    if prefix == 0:
        return True
    win = min(64 * 1024, prefix)
    offsets = {prefix - win}
    for _ in range(windows):
        offsets.add(random.randrange(0, max(prefix - win, 1)))
    with open(path, "rb") as fh:
        for off in sorted(offsets):
            fh.seek(off)
            local = fh.read(win)
            for attempt in range(5):
                try:
                    r = open_range(opener, url, off, off + win - 1, timeout, size)
                    remote = r.read()
                    r.close()
                    break
                except FatalHTTPError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log(f"spot-check retry {attempt + 1} at {off}: {exc}")
                    time.sleep(5 * (attempt + 1))
            else:
                log("spot-check could not reach server; aborting adoption")
                return False
            if local != remote:
                log(f"spot-check MISMATCH at offset {off} (window {win})")
                return False
            log(f"spot-check OK at offset {off} ({win} bytes)")
    return True


def cmd_download(args) -> int:
    out = os.path.abspath(args.out)
    part = out + ".part"
    state_path = part + ".state.json"
    log = Logger(args.log or out + ".download.log")
    opener = build_opener()

    if os.path.exists(out) and os.path.exists(out + ".md5ok"):
        log(f"{out} already verified (see {out}.md5ok); nothing to do")
        return 0

    for candidate in (out, part):
        holders = other_open_handles(candidate)
        if holders:
            log(f"REFUSING: {candidate} is open by {holders}. Stop that process first.")
            return 2

    if os.path.exists(state_path):
        state = State.load(state_path)
        if state.data["size"] != args.size or (args.md5 and state.data["md5"] != args.md5):
            log("REFUSING: existing state file describes a different file (size/md5 mismatch)")
            return 2
        log(f"resuming from state: {human(state.completed_bytes())} / {human(state.data['size'])}")
    else:
        if os.path.exists(part):
            if not args.adopt_part:
                log(f"REFUSING: {part} exists without a state file; rerun with --adopt-part to treat it "
                    "as a sequential prefix (it will be spot-checked first)")
                return 2
            src = part
        elif os.path.exists(out):
            src = out
        else:
            src = None
        prefix = os.path.getsize(src) if src else 0
        if prefix > args.size:
            log(f"REFUSING: {src} is larger ({prefix}) than the expected size ({args.size})")
            return 2
        if src:
            with open(src, "rb") as fh:
                magic = fh.read(len(bytes.fromhex(args.magic))) if args.magic else b""
            if args.magic and prefix >= len(magic) and magic != bytes.fromhex(args.magic):
                log(f"REFUSING: {src} does not start with expected magic {args.magic}; got {magic.hex()}")
                return 2
            log(f"found existing sequential prefix {src} = {human(prefix)}; spot-checking against server")
            if not spot_check_prefix(opener, args.url, src, prefix, args.size, args.timeout, log):
                log("REFUSING to adopt prefix that does not match the server. File left untouched.")
                return 2
            if src == out:
                os.rename(out, part)
                log(f"renamed {out} -> {part} (no data copied)")
        state = State.new(state_path, args.url, args.size, args.md5, args.chunk_mb * MiB, prefix)
        state.save()
        log(f"created state file {state_path} ({len(state.data['done'])} chunks of {args.chunk_mb} MiB)")

    remaining = state.data["size"] - state.completed_bytes()
    free = shutil.disk_usage(os.path.dirname(out)).free
    if free < remaining + 2 * 1024 * MiB:
        log(f"REFUSING: free disk {human(free)} < remaining {human(remaining)} + 2 GB margin")
        return 2
    log(f"disk free {human(free)}; remaining {human(remaining)}; connections={args.connections}")

    fd = os.open(part, os.O_RDWR | os.O_CREAT, 0o644)  # never O_TRUNC
    stop = threading.Event()
    fatal: list[str] = []
    pending = state.pending()
    random.shuffle(pending) if args.shuffle else None
    queue_lock = threading.Lock()
    in_flight: set[int] = set()
    stats = {"retries": 0, "session_bytes": 0}
    stats_lock = threading.Lock()

    def next_chunk() -> int | None:
        with queue_lock:
            while pending:
                i = pending.pop(0)
                if state.data["done"][i] < state.chunk_len(i):
                    in_flight.add(i)
                    return i
            return None

    def worker(wid: int) -> None:
        backoff = 5.0
        range_errors = 0
        while not stop.is_set():
            i = next_chunk()
            if i is None:
                return
            c_start, c_end = state.chunk_bounds(i)
            while not stop.is_set() and state.data["done"][i] < state.chunk_len(i):
                offset = c_start + state.data["done"][i]
                try:
                    resp = open_range(opener, args.url, offset, c_end, args.timeout, state.data["size"])
                    while not stop.is_set():
                        buf = resp.read(BLOCK)
                        if not buf:
                            break
                        if offset + len(buf) > c_end + 1:
                            raise RangeError("server sent more bytes than requested")
                        os.pwrite(fd, buf, offset)
                        offset += len(buf)
                        with state.lock:
                            state.data["done"][i] = offset - c_start
                        with stats_lock:
                            stats["session_bytes"] += len(buf)
                    resp.close()
                    backoff = 5.0
                    range_errors = 0
                    if state.data["done"][i] < state.chunk_len(i) and not stop.is_set():
                        raise ConnectionError("connection closed early")
                except FatalHTTPError as exc:
                    fatal.append(str(exc))
                    log(f"worker {wid}: FATAL {exc}")
                    stop.set()
                    return
                except (RangeError, urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                        OSError, ValueError) as exc:
                    if stop.is_set():
                        break
                    if isinstance(exc, RangeError):
                        range_errors += 1
                        if range_errors >= 5:
                            fatal.append(f"repeated invalid range responses: {exc}")
                            log(f"worker {wid}: FATAL repeated invalid range responses: {exc}")
                            stop.set()
                            return
                    with stats_lock:
                        stats["retries"] += 1
                    wait = backoff + random.uniform(0, backoff / 2)
                    log(f"worker {wid}: chunk {i} at {offset}: {type(exc).__name__}: {exc}; retry in {wait:.0f}s")
                    stop.wait(wait)
                    backoff = min(backoff * 2, 120.0)
            with queue_lock:
                in_flight.discard(i)

    def handle_signal(signum, _frame):
        log(f"received signal {signum}; checkpointing and stopping")
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(args.connections)]
    t0 = time.time()
    for th in threads:
        th.start()

    status_path = part + ".status.json"
    last_t, last_b = t0, 0
    last_save = 0.0
    while any(th.is_alive() for th in threads):
        time.sleep(1)
        t = time.time()
        if t - last_save >= args.checkpoint_s:
            os.fsync(fd)
            state.save()
            last_save = t
        if t - last_t >= args.report_s:
            with stats_lock:
                sb = stats["session_bytes"]
                retries = stats["retries"]
            done = state.completed_bytes()
            size = state.data["size"]
            inst = (sb - last_b) / (t - last_t)
            avg = sb / (t - t0)
            eta = (size - done) / avg if avg > 0 else float("inf")
            msg = (f"progress {done / size * 100:6.2f}% {human(done)}/{human(size)} "
                   f"speed {inst / 1000:.0f} KB/s (session avg {avg / 1000:.0f} KB/s) "
                   f"ETA {hms(eta)} active={len(in_flight)} retries={retries}")
            log(msg)
            with open(status_path, "w", encoding="utf-8") as fh:
                json.dump({"time": now(), "done": done, "size": size, "percent": done / size * 100,
                           "inst_Bps": inst, "avg_Bps": avg, "eta_s": eta, "retries": retries,
                           "pid": os.getpid()}, fh)
            last_t, last_b = t, sb

    os.fsync(fd)
    os.close(fd)
    state.save()

    if fatal:
        log(f"stopped on fatal error(s): {fatal}")
        return 3
    if state.pending():
        log(f"stopped with {len(state.pending())} chunks pending; rerun the same command to resume")
        return 1

    actual = os.path.getsize(part)
    if actual != state.data["size"]:
        log(f"SIZE MISMATCH after completion: {actual} != {state.data['size']}; keeping .part")
        return 4
    log(f"all bytes received in this session ({human(stats['session_bytes'])} in {hms(time.time() - t0)}); verifying")
    if state.data.get("md5"):
        digest = md5_file(part, log)
        if digest != state.data["md5"]:
            log(f"MD5 MISMATCH: got {digest}, expected {state.data['md5']}; keeping {part} and state for inspection")
            return 5
        log(f"MD5 OK {digest}")
    os.rename(part, out)
    with open(out + ".md5ok", "w", encoding="utf-8") as fh:
        fh.write(f"{state.data.get('md5', '')}  {os.path.basename(out)}\nverified {now()}\n")
    os.rename(state_path, state_path + ".done")
    log(f"DONE: {out}")
    return 0


def cmd_status(args) -> int:
    out = os.path.abspath(args.out)
    part = out + ".part"
    if os.path.exists(out + ".md5ok"):
        print(open(out + ".md5ok", encoding="utf-8").read().strip())
        return 0
    state_path = part + ".state.json"
    if os.path.exists(state_path):
        st = State.load(state_path)
        done, size = st.completed_bytes(), st.data["size"]
        print(f"state: {done / size * 100:.2f}% ({human(done)} / {human(size)}), "
              f"{len(st.pending())} chunks pending, updated {st.data.get('updated')}")
    else:
        target = part if os.path.exists(part) else out
        if os.path.exists(target):
            print(f"no state file; {target} is {human(os.path.getsize(target))} (sequential prefix?)")
        else:
            print("nothing downloaded yet")
    status_path = part + ".status.json"
    if os.path.exists(status_path):
        s = json.load(open(status_path, encoding="utf-8"))
        print(f"last report {s['time']}: {s['inst_Bps'] / 1000:.0f} KB/s (avg {s['avg_Bps'] / 1000:.0f} KB/s), "
              f"ETA {hms(s['eta_s'])}, retries={s['retries']}, pid={s['pid']}")
    holders = other_open_handles(part) or other_open_handles(out)
    print(f"processes holding the file: {holders or 'none'}")
    return 0


def cmd_verify(args) -> int:
    log = Logger(None)
    digest = md5_file(args.file, log)
    ok = digest == args.md5
    print(f"{digest}  {'OK' if ok else 'MISMATCH (expected ' + args.md5 + ')'}")
    return 0 if ok else 5


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("probe")
    pp.add_argument("--url", action="append", required=True)
    pp.add_argument("--size", type=int, default=None)
    pp.add_argument("--seconds", type=float, default=20)
    pp.add_argument("--connections", type=int, nargs="+", default=[1, 4])
    pp.add_argument("--timeout", type=float, default=60)
    pp.add_argument("--log", default=None)
    pp.set_defaults(func=cmd_probe)

    pd = sub.add_parser("download")
    pd.add_argument("--url", required=True)
    pd.add_argument("--out", required=True)
    pd.add_argument("--size", type=int, required=True)
    pd.add_argument("--md5", default=None)
    pd.add_argument("--magic", default=None, help="expected leading bytes as hex, e.g. 1f8b for gzip")
    pd.add_argument("--connections", type=int, default=6)
    pd.add_argument("--chunk-mb", type=int, default=64)
    pd.add_argument("--timeout", type=float, default=60, help="connect/read timeout in seconds")
    pd.add_argument("--checkpoint-s", type=float, default=5)
    pd.add_argument("--report-s", type=float, default=30)
    pd.add_argument("--shuffle", action="store_true")
    pd.add_argument("--adopt-part", action="store_true")
    pd.add_argument("--log", default=None)
    pd.set_defaults(func=cmd_download)

    ps = sub.add_parser("status")
    ps.add_argument("--out", required=True)
    ps.set_defaults(func=cmd_status)

    pv = sub.add_parser("verify")
    pv.add_argument("--file", required=True)
    pv.add_argument("--md5", required=True)
    pv.set_defaults(func=cmd_verify)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
