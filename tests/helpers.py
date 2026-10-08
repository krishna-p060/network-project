"""Shared test pieces: paths, the SPEC.md example bytes, frame builders and readers,
and bserve/bcurl subprocess runners.

The builders and readers are written straight from SPEC.md and do not use
bproto, so a bug in bproto cannot hide by being on both sides of a test.
"""

import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSERVE = os.path.join(ROOT, "bserve")
BCURL = os.path.join(ROOT, "bcurl")
WWW = os.path.join(ROOT, "www")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HEADERS, DATA, GOAWAY = 0x01, 0x02, 0x03
END_STREAM = 0x01
STATIC = (
    None,
    ":method",
    ":path",
    ":status",
    "host",
    "content-type",
    "content-length",
    "user-agent",
    "accept",
    "server",
    "date",
)
INDEX = {name: index for index, name in enumerate(STATIC) if name}

# SPEC.md section 7, typed in by hand.
SPEC_REQUEST = bytes.fromhex(
    "00 00 00 37 01 01 00 01 "  # Length 55, HEADERS, END_STREAM, stream 1
    "01 00 03 47 45 54 "  # :method "GET"
    "02 00 0b 2f 69 6e 64 65 78 2e 68 74 6d 6c "  # :path "/index.html"
    "04 00 0e 6c 6f 63 61 6c 68 6f 73 74 3a 39 30 30 30 "  # host "localhost:9000"
    "07 00 09 62 63 75 72 6c 2f 31 2e 30 "  # user-agent "bcurl/1.0"
    "08 00 03 2a 2f 2a"  # accept "*/*"
)
SPEC_RESPONSE = bytes.fromhex(
    "00 00 00 44 01 00 00 01 "  # Length 68, HEADERS, no flags, stream 1
    "03 00 03 32 30 30 "  # :status "200"
    "05 00 09 74 65 78 74 2f 68 74 6d 6c "  # content-type "text/html"
    "06 00 02 31 32 "  # content-length "12"
    "09 00 0a 62 73 65 72 76 65 2f 31 2e 30 "  # server "bserve/1.0"
    "0a 00 1d 54 68 75 2c 20 30 38 20 4f 63 74 20 32 30 32 36 20 "  # date "Thu, 08 Oct 2026 "
    "31 33 3a 30 30 3a 30 30 20 47 4d 54 "  # "13:00:00 GMT"
    "00 00 00 0c 02 01 00 01 "  # Length 12, DATA, END_STREAM, stream 1
    "3c 68 31 3e 48 69 3c 2f 68 31 3e 0a"  # "<h1>Hi</h1>\n"
)

Frame = namedtuple("Frame", "type flags stream payload raw")
Response = namedtuple("Response", "status headers body frames")


def frame(ftype, flags, stream_id, payload=b""):
    return len(payload).to_bytes(4, "big") + bytes((ftype, flags)) + stream_id.to_bytes(2, "big") + payload


def entry(name, value):
    """One header entry, by static index when the name has one."""
    raw = value.encode()
    head = bytes((INDEX[name],)) if name in INDEX else bytes((0, len(name))) + name.encode()
    return head + len(raw).to_bytes(2, "big") + raw


def block(*pairs):
    return b"".join(entry(name, value) for name, value in pairs)


def request(path, stream_id=1, method="GET", host="localhost:9000"):
    return frame(HEADERS, END_STREAM, stream_id, block((":method", method), (":path", path), ("host", host)))


def response(status, body=b"", stream_id=1, content_type="text/plain"):
    """A whole response: HEADERS, then one DATA frame unless the body is empty."""
    head = block((":status", str(status)), ("content-type", content_type), ("content-length", str(len(body))))
    if not body:
        return frame(HEADERS, END_STREAM, stream_id, head)
    return frame(HEADERS, 0, stream_id, head) + frame(DATA, END_STREAM, stream_id, body)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


def recv_frame(sock):
    header = recv_exact(sock, 8)
    payload = recv_exact(sock, int.from_bytes(header[:4], "big"))
    return Frame(header[4], header[5], int.from_bytes(header[6:8], "big"), payload, header + payload)


def parse_block(payload):
    pairs, pos = [], 0
    while pos < len(payload):
        if payload[pos] == 0:
            size = payload[pos + 1]
            name = payload[pos + 2 : pos + 2 + size].decode()
            pos += 2 + size
        else:
            name = STATIC[payload[pos]]
            pos += 1
        size = int.from_bytes(payload[pos : pos + 2], "big")
        pairs.append((name, payload[pos + 2 : pos + 2 + size].decode()))
        pos += 2 + size
    return pairs


def recv_response(sock):
    """Read frames up to END_STREAM and return the decoded response."""
    frames = []
    while not frames or not (frames[-1].type in (HEADERS, DATA) and frames[-1].flags & END_STREAM):
        frames.append(recv_frame(sock))
    head = next(f for f in frames if f.type == HEADERS)
    headers = dict(parse_block(head.payload))
    body = b"".join(f.payload for f in frames if f.type == DATA)
    return Response(int(headers[":status"]), headers, body, frames)


def run_bcurl(*args, direct=False):
    """Run bcurl; direct=True runs the file itself, through its #! line."""
    command = [BCURL] if direct else [sys.executable, BCURL]
    return subprocess.run([*command, *args], capture_output=True, timeout=30)


class ServerProcess:
    """bserve in a subprocess, listening on a free port on 127.0.0.1."""

    def __init__(self, root, *options, direct=False):
        command = [BSERVE] if direct else [sys.executable, BSERVE]
        self.proc = subprocess.Popen(
            [*command, root, "0", "--bind", "127.0.0.1", *options],
            stderr=subprocess.PIPE,
            text=True,
        )
        first = self.proc.stderr.readline()
        match = re.search(r":(\d+)$", first.strip())
        if not match:
            self.proc.kill()
            raise RuntimeError(f"bserve did not start: {first!r}")
        self.port = int(match.group(1))
        self.log = []
        self.drain = threading.Thread(target=self._drain, daemon=True)
        self.drain.start()

    def _drain(self):
        for line in self.proc.stderr:
            self.log.append(line.rstrip("\n"))

    def connect(self):
        return socket.create_connection(("127.0.0.1", self.port), timeout=5)

    def wait_for_log(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        while len(self.log) < count and time.monotonic() < deadline:
            time.sleep(0.02)
        return list(self.log)

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.drain.join(timeout=5)
        self.proc.stderr.close()
