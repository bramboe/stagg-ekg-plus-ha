#!/usr/bin/env python3
"""Probe a Fellow Stagg EKG Pro's local HTTP interface for anything that still answers.

Firmware 1.2.24 (Sep 30 2026) made /cli?cmd=<anything> return the bare form page instead of
command output (bramboe/stagg-ekg-plus-ha#5). This script sends variations of *read-only*
commands, request encodings, parameter names, paths and headers, and reports every response
that differs from the known dead responses (form page, 404, /api "cannot parse").

Safety: every command passes is_read_only() before it is sent. The community reference
(montymhughes/stagg-ekg-pro) lists commands that look harmless but are not: bare `ss` and
`adcsamples` reboot the kettle, and single characters (`1`, `2u`, `q`, `w`) inject button
presses. Only exact allowlisted query commands get through. --write-test is the only mode that
changes anything: it sends short `buz` beeps, to find out whether commands still run but with
their output suppressed (by response time, then by ear if run in a terminal).

Usage: python3 probe_cli.py IP [options]
Examples:
  python3 probe_cli.py 192.168.1.86                  # read-only sweep
  python3 probe_cli.py 192.168.1.86 --only commands,encoding
  python3 probe_cli.py 192.168.1.86 --timing 6       # do commands still run? compare response times
  python3 probe_cli.py 192.168.1.86 --write-test     # beep test, stand next to the kettle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import statistics
import sys
import time
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import unquote_plus

# Query-only commands from the community reference, plus `help` (prints the command list).
READ_ONLY_COMMANDS = (
    "state", "prtsaved", "prtsettings", "prtclock", "commands", "fwinfo", "temp", "heapprt",
    "lvglinfo", "wifiprt", "logprt", "pwmprt", "help",
)

# Informational names only; nothing that sounds like an action (reset, ota, update, erase, ...).
INFO_PATHS = (
    "/index.html", "/favicon.ico", "/info", "/status", "/state", "/version", "/fw", "/fwinfo",
    "/settings", "/config", "/log", "/logs", "/debug", "/console", "/output", "/result", "/cli/out",
    "/cli/output", "/cli/log", "/cli.txt", "/json", "/data", "/metrics", "/heap", "/events", "/ws",
    "/wifi", "/proto-ver", "/api/v1", "/api/v2", "/api/info", "/api/status",
)

BEEP = "buz 400 1000 200"  # what the integration's beep button sends

# Same markers the integration's config flow uses to recognise real `state` output.
STATE_MARKERS = (b"mode=", b"tempr")


def is_read_only(cmd: str) -> bool:
    """True only if cmd, after decoding and stripping padding/quotes/terminators, is allowlisted."""
    word = unquote_plus(cmd).strip(" \t\r\n\0;\"'").lower()
    return word in READ_ONLY_COMMANDS


def http_request(
    host: str,
    target: str,
    method: str = "GET",
    headers: dict | None = None,
    body: bytes = b"",
    version: str = "HTTP/1.1",
) -> bytes:
    """Build raw request bytes so nothing (urllib, aiohttp) normalises the target or headers."""
    h = {"Host": host, "User-Agent": "stagg-cli-probe/1.0", "Accept": "*/*", "Connection": "close"}
    h.update(headers or {})
    if body:
        h["Content-Length"] = str(len(body))
    lines = [f"{method} {target} {version}"] + [f"{k}: {v}" for k, v in h.items() if v is not None]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


@dataclass
class Probe:
    group: str
    label: str
    request: bytes
    cmd: str | None = None  # command carried by the request, checked by is_read_only()
    followup: bytes | None = None  # sent on the same connection after the first response
    linger: float = 0.0  # keep reading this long after the response looks complete
    write: bool = False


@dataclass
class Result:
    status: int | None
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    elapsed: float = 0.0
    error: str | None = None
    trailing: bytes = b""


def _response_complete(buf: bytes) -> bool:
    end = buf.find(b"\r\n\r\n")
    if end < 0:
        return False
    head, body = buf[:end].lower(), buf[end + 4 :]
    m = re.search(rb"content-length:\s*(\d+)", head)
    if m:
        return len(body) >= int(m.group(1))
    if b"chunked" in head:
        return body.endswith(b"0\r\n\r\n")
    return False  # no framing: read until the server closes


def _dechunk(body: bytes) -> bytes:
    out, i = b"", 0
    try:
        while True:
            j = body.index(b"\r\n", i)
            size = int(body[i:j].split(b";")[0].strip() or b"0", 16)
            if size == 0:
                return out
            out += body[j + 2 : j + 2 + size]
            i = j + 2 + size + 2
    except ValueError:
        return out or body


def _parse(buf: bytes) -> tuple[int | None, dict, bytes]:
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    m = re.match(r"HTTP/\d\.\d (\d{3})", lines[0])
    headers = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = _dechunk(body)
    return (int(m.group(1)) if m else None), headers, body


def _exchange(sock: socket.socket, raw: bytes, timeout: float, linger: float = 0.0) -> Result:
    start = time.monotonic()
    buf, error = b"", None
    try:
        sock.sendall(raw)
        sock.settimeout(timeout)
        while not _response_complete(buf):
            data = sock.recv(4096)
            if not data:
                break
            buf += data
    except OSError as err:
        error = f"{type(err).__name__}: {err}" if not buf else "incomplete response"
    elapsed = time.monotonic() - start
    if not buf:
        return Result(None, elapsed=elapsed, error=error or "empty response")

    trailing = b""
    deadline = time.monotonic() + linger
    while time.monotonic() < deadline:
        try:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            data = sock.recv(4096)
        except OSError:
            break
        if not data:
            break
        trailing += data

    status, headers, body = _parse(buf)
    return Result(status, headers, body, elapsed, error, trailing)


def run_probe(args: argparse.Namespace, probe: Probe) -> list[Result]:
    if probe.cmd is not None and not probe.write and not is_read_only(probe.cmd):
        raise SystemExit(f"refusing to send non-read-only command {probe.cmd!r} ({probe.label})")
    start = time.monotonic()
    try:
        sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    except OSError as err:
        return [Result(None, elapsed=time.monotonic() - start, error=f"connect: {err}")]
    with sock:
        results = [_exchange(sock, probe.request, args.timeout, probe.linger)]
        if probe.followup:
            results.append(_exchange(sock, probe.followup, args.timeout))
    return results


def build_probes(host: str) -> list[Probe]:
    req = partial(http_request, host)
    probes: list[Probe] = []
    add = lambda *a, **kw: probes.append(Probe(*a, **kw))  # noqa: E731

    # Every known read-only command, encoded the way the integration does it.
    for cmd in READ_ONLY_COMMANDS:
        add("commands", cmd, req(f"/cli?cmd={cmd}"), cmd=cmd)

    # Line terminators, case, padding, quoting, full percent-encoding.
    for base in ("state", "commands"):
        for enc in (
            f"{base}%0A",
            f"{base}%0D%0A",
            f"{base}%0D",
            f"{base}%00",
            f"{base}%3B",
            base.upper(),
            base.capitalize(),
            f"+{base}",
            f"{base}+",
            f"%20{base}%20",
            f"%22{base}%22",
            "".join(f"%{ord(c):02X}" for c in base),
        ):
            add("encoding", f"cmd={enc}", req(f"/cli?cmd={enc}"), cmd=enc)
    add("encoding", "cmd=state&cmd=state", req("/cli?cmd=state&cmd=state"), cmd="state")
    add("encoding", "cmd=state&", req("/cli?cmd=state&"), cmd="state")
    add("encoding", "?&cmd=state", req("/cli?&cmd=state"), cmd="state")

    # Other parameter names (the form's <label> points at "x", the <input> id is "cli").
    for name in ("cli", "x", "command", "c", "q", "CMD", "Cmd", "cmd%5B%5D"):
        add("param", f"{name}=state", req(f"/cli?{name}=state"), cmd="state")

    # Path shapes.
    for target in (
        "/cli/state",
        "/cli?state",
        "/cli/?cmd=state",
        "/CLI?cmd=state",
        "//cli?cmd=state",
        "/cli%3Fcmd=state",
        "/cli/cmd/state",
        "/cli/cmd?cmd=state",
    ):
        add("path", target, req(target), cmd="state")

    # Protocol version and headers.
    add(
        "headers",
        "HTTP/1.0, no Host",
        req("/cli?cmd=state", headers={"Host": None, "Connection": None}, version="HTTP/1.0"),
        cmd="state",
    )
    for label, hdrs in (
        ("keep-alive", {"Connection": "keep-alive"}),
        ("Accept: text/plain", {"Accept": "text/plain"}),
        ("Accept: application/json", {"Accept": "application/json"}),
        ("Referer: /cli", {"Referer": f"http://{host}/cli"}),
        ("Origin", {"Origin": f"http://{host}"}),
        ("X-Requested-With", {"X-Requested-With": "XMLHttpRequest"}),
        (
            "UA: Safari",
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
            },
        ),
        ("UA: iOS app", {"User-Agent": "Fellow/1 CFNetwork/1568.100.1 Darwin/24.0.0"}),
        ("UA: Android app", {"User-Agent": "okhttp/4.12.0"}),
    ):
        add("headers", label, req("/cli?cmd=state", headers=hdrs), cmd="state")

    # Delayed or session-bound output: keep listening, then ask again on the same socket.
    for cmd in ("state", "logprt"):
        add(
            "late",
            f"{cmd}, linger 3s, then GET /cli same socket",
            req(f"/cli?cmd={cmd}", headers={"Connection": "keep-alive"}),
            cmd=cmd,
            linger=3.0,
            followup=req("/cli"),
        )
    add(
        "late",
        "state, then GET / same socket",
        req("/cli?cmd=state", headers={"Connection": "keep-alive"}),
        cmd="state",
        followup=req("/"),
    )

    # /api with the command in the obvious places (issue #5's 114 probes found nothing; spot-check).
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    as_json = {"Content-Type": "application/json"}
    add("api", "GET /api?cmd=state", req("/api?cmd=state"), cmd="state")
    add("api", "GET /api/state", req("/api/state"), cmd="state")
    add("api", "POST /api text", req("/api", "POST", {"Content-Type": "text/plain"}, b"state"), cmd="state")
    add(
        "api",
        "POST /api text+LF",
        req("/api", "POST", {"Content-Type": "text/plain"}, b"state\n"),
        cmd="state",
    )
    add("api", "POST /api form", req("/api", "POST", form, b"cmd=state"), cmd="state")
    for key in ("cmd", "command", "cli"):
        add(
            "api",
            f'POST /api {{"{key}":"state"}}',
            req("/api", "POST", as_json, json.dumps({key: "state"}).encode()),
            cmd="state",
        )
    add("api", "POST /cli form", req("/cli", "POST", form, b"cmd=state"), cmd="state")

    # Other endpoints (plain GETs, no command).
    for path in INFO_PATHS:
        add("paths", path, req(path))
    return probes


def build_write_probes(host: str) -> list[Probe]:
    req = partial(http_request, host)
    enc = BEEP.replace(" ", "+")
    return [
        Probe("write", f"/cli?cmd={enc}", req(f"/cli?cmd={enc}"), cmd=BEEP, write=True),
        Probe("write", f"/cli?cmd={enc}%0A", req(f"/cli?cmd={enc}%0A"), cmd=BEEP, write=True),
        Probe(
            "write",
            "/cli?cmd=buz%20400%201000%20200",
            req("/cli?cmd=buz%20400%201000%20200"),
            cmd=BEEP,
            write=True,
        ),
        Probe(
            "write",
            'POST /api {"cmd":"buz ..."}',
            req("/api", "POST", {"Content-Type": "application/json"}, json.dumps({"cmd": BEEP}).encode()),
            cmd=BEEP,
            write=True,
        ),
    ]


def fingerprint(r: Result) -> tuple:
    return r.status, hashlib.sha1(r.body).hexdigest()


def looks_like_state(data: bytes) -> bool:
    low = data.lower()
    return all(m in low for m in STATE_MARKERS)


def record(out, probe: Probe, step: int, r: Result, known: str, tags: list[str]) -> None:
    out.write(
        json.dumps(
            {
                "group": probe.group,
                "label": probe.label,
                "step": step,
                "request": (probe.request if step == 0 else probe.followup or b"").decode("latin-1"),
                "status": r.status,
                "headers": r.headers,
                "elapsed_ms": round(r.elapsed * 1000),
                "body": r.body.decode("utf-8", "replace")[:4000],
                "trailing": r.trailing.decode("utf-8", "replace")[:4000],
                "error": r.error,
                "known_as": known,
                "tags": tags,
            }
        )
        + "\n"
    )


def sweep(args: argparse.Namespace) -> int:
    refs: dict[tuple, str] = {}
    timings = []
    req = partial(http_request, args.host)
    reference_probes = [Probe("ref", "form", req("/cli?cmd=state"), cmd="state")] * 5 + [
        Probe("ref", "404", req("/__stagg_probe_nonexistent__")),
        Probe("ref", "api-400", req("/api")),
        Probe("ref", "index", req("/")),
    ]
    print(f"Reference responses from {args.host}:")
    for probe in reference_probes:
        r = run_probe(args, probe)[0]
        if r.error and r.status is None:
            print(f"  {probe.label}: {r.error} - is the kettle reachable?")
            return 2
        if probe.label == "form":
            timings.append(r.elapsed)
            if looks_like_state(r.body):
                print("  /cli?cmd=state returns real state output - the CLI works on this firmware.")
        refs.setdefault(fingerprint(r), probe.label)
        time.sleep(args.delay)
    baseline = statistics.median(timings)
    for (status, digest), label in refs.items():
        print(f"  {label:8} HTTP {status}  sha1 {digest[:10]}")
    print(f"  median /cli time {baseline * 1000:.0f} ms\n")

    groups = set(args.only.split(",")) if args.only else None
    probes = [p for p in build_probes(args.host) if groups is None or p.group in groups]
    interesting = []
    errors_in_a_row = 0
    with open(args.out, "w") as out:
        for probe in probes:
            results = run_probe(args, probe)
            for step, r in enumerate(results):
                known = refs.get(fingerprint(r), "")
                tags = []
                if r.status is None:
                    tags.append(f"ERROR {r.error}")
                else:
                    if looks_like_state(r.body + r.trailing):
                        tags.append("STATE OUTPUT")
                    if not known:
                        tags.append("NEW")
                    if r.trailing:
                        tags.append(f"TRAILING {len(r.trailing)}B")
                    if r.error:
                        tags.append(r.error.upper())
                    if r.elapsed > max(3 * baseline, baseline + 0.5):
                        tags.append("SLOW")
                label = (
                    probe.label if step == 0 else f"  -> followup {probe.followup.split(b' ')[1].decode()}"
                )
                status = r.status if r.status is not None else "---"
                print(
                    f"{probe.group:9} {label[:46]:46} {status} {len(r.body):5}B {r.elapsed * 1000:6.0f}ms  "
                    f"{known:8} {' '.join(tags)}"
                )
                record(out, probe, step, r, known, tags)
                if tags:
                    interesting.append((probe, step, r, tags))
            errors_in_a_row = errors_in_a_row + 1 if results[0].status is None else 0
            if errors_in_a_row >= 5:
                print("\nFive failures in a row; stopping so the kettle isn't hammered.")
                break
            time.sleep(args.delay)

    print(f"\n{len(probes)} probes, full log in {args.out}")
    if not interesting:
        print("Nothing differed from the reference responses.")
        return 0
    print(f"\n{len(interesting)} response(s) worth a look:")
    for probe, step, r, tags in interesting:
        target = (probe.request if step == 0 else probe.followup).split(b"\r\n")[0].decode("latin-1")
        print(f"\n[{probe.group}] {target}  ({' '.join(tags)})")
        print(f"  HTTP {r.status}  {dict(list(r.headers.items())[:4])}")
        snippet = (r.body + r.trailing).decode("utf-8", "replace").strip()
        print("  " + (snippet[:400].replace("\n", "\n  ") or "(empty body)"))
    return 1


def timing(args: argparse.Namespace) -> int:
    """Time each read-only command against an empty command, round-robin to spread out noise.

    If the firmware still runs commands but sends their output to the serial console instead of
    HTTP, chatty commands (logprt, heapprt) take visibly longer than the empty control.
    """
    req = partial(http_request, args.host)
    probes = [Probe("timing", "(empty)", req("/cli?cmd="))]
    probes += [Probe("timing", cmd, req(f"/cli?cmd={cmd}"), cmd=cmd) for cmd in READ_ONLY_COMMANDS]
    samples: dict[str, list[float]] = {p.label: [] for p in probes}
    for n in range(args.timing):
        print(f"round {n + 1}/{args.timing}", end="\r", flush=True)
        for probe in probes:
            r = run_probe(args, probe)[0]
            if r.status is not None:
                samples[probe.label].append(r.elapsed * 1000)
            time.sleep(args.delay)
    control = statistics.median(samples["(empty)"])
    print(f"{'command':12} {'median':>7} {'min':>6} {'max':>6}   vs empty ({control:.0f} ms)")
    for label, ms in samples.items():
        if not ms:
            print(f"{label:12} no responses")
            continue
        med = statistics.median(ms)
        flag = "  <- slower" if min(ms) > control + 100 else ""
        print(f"{label:12} {med:7.0f} {min(ms):6.0f} {max(ms):6.0f}   {med - control:+6.0f}{flag}")
    return 0


def beep_timing(args: argparse.Namespace) -> bool:
    """Send a 200 ms and an 800 ms beep twice each; True if the response time tracks the duration.

    The CLI handler runs commands synchronously (logprt holds the response ~1.3 s), so if `buz`
    executes and blocks for its duration, the long beep comes back ~600 ms later than the short one.
    """
    req = partial(http_request, args.host)
    times: dict[int, list[float]] = {200: [], 800: []}
    for dur in (200, 800, 200, 800):
        cmd = f"buz 400 1000 {dur}"
        r = run_probe(
            args, Probe("write", cmd, req(f"/cli?cmd={cmd.replace(' ', '+')}"), cmd=cmd, write=True)
        )[0]
        if r.status is not None:
            times[dur].append(r.elapsed * 1000)
        time.sleep(1.5)
    if not times[200] or not times[800]:
        print("Beep timing: no responses")
        return False
    short, long_ = statistics.median(times[200]), statistics.median(times[800])
    print(
        f"Beep timing: 200 ms beep answered in {short:.0f} ms, 800 ms beep in {long_:.0f} ms "
        f"({long_ - short:+.0f} ms)"
    )
    return long_ - short > 400


def write_test(args: argparse.Namespace) -> int:
    print(f"Write test: sends `buz` (short beeps) to {args.host}. This is the only mode that changes")
    print("kettle state, and a beep is all it does. If commands still execute with their output")
    print("suppressed, the response time follows the beep length and you hear the beeps.\n")
    if beep_timing(args):
        print("-> buz executes: the CLI still runs commands, only the HTTP output is gone.")
    else:
        print("-> no timing evidence that buz executes (it may run asynchronously; listen instead).")
    if not sys.stdin.isatty():
        return 0
    input("\nNext: one beep per request shape. Stand next to the kettle and press Enter... ")
    heard = []
    for probe in build_write_probes(args.host):
        r = run_probe(args, probe)[0]
        body = "form page" if b"CLI Command" in r.body else r.body[:60].decode("utf-8", "replace")
        print(f"\nsent {probe.label}: HTTP {r.status}, {len(r.body)}B ({body})")
        if input("  Did it beep? [y/N] ").strip().lower().startswith("y"):
            heard.append(probe.label)
        time.sleep(1)
    print("\nBeeped for: " + (", ".join(heard) if heard else "none"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host", help="kettle IP address, e.g. 192.168.1.86")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between probes")
    parser.add_argument(
        "--only", help="comma-separated groups: commands,encoding,param,path,headers,late,api,paths"
    )
    parser.add_argument("--out", default=f"probe_cli_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    parser.add_argument("--timing", type=int, metavar="N", help="time each read-only command over N rounds")
    parser.add_argument("--write-test", action="store_true", help="interactive beep test (changes state)")
    args = parser.parse_args()
    if args.write_test:
        return write_test(args)
    return timing(args) if args.timing else sweep(args)


if __name__ == "__main__":
    sys.exit(main())
