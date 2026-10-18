"""Prometheus metrics and health endpoints for bserve, on a separate plain-HTTP port.

BHTTP/1 is not HTTP, so Prometheus and the kubelet cannot talk to the main port.
This module serves /metrics, /healthz and /readyz over ordinary HTTP instead.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DURATION_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


def _escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metrics:
    """Thread-safe counters, one gauge and one histogram, rendered in Prometheus text format."""

    def __init__(self, version):
        self.version = version
        self.lock = threading.Lock()
        self.requests = {}  # (method, status) -> count
        self.bytes_sent = 0
        self.connections_total = 0
        self.connections_open = 0
        self.protocol_errors = 0
        self.buckets = [0] * len(DURATION_BUCKETS)
        self.duration_sum = 0.0
        self.duration_count = 0

    def connection_opened(self):
        with self.lock:
            self.connections_total += 1
            self.connections_open += 1

    def connection_closed(self):
        with self.lock:
            self.connections_open -= 1

    def protocol_error(self):
        with self.lock:
            self.protocol_errors += 1

    def observe(self, method, status, seconds, body_bytes):
        with self.lock:
            key = (method, str(status))
            self.requests[key] = self.requests.get(key, 0) + 1
            self.bytes_sent += body_bytes
            self.duration_sum += seconds
            self.duration_count += 1
            for i, bound in enumerate(DURATION_BUCKETS):
                if seconds <= bound:
                    self.buckets[i] += 1

    def render(self):
        with self.lock:
            out = [
                "# HELP bserve_build_info Running version of bserve.",
                "# TYPE bserve_build_info gauge",
                f'bserve_build_info{{version="{_escape(self.version)}"}} 1',
                "# HELP bserve_requests_total BHTTP/1 requests answered, by method and status.",
                "# TYPE bserve_requests_total counter",
            ]
            for (method, status), count in sorted(self.requests.items()):
                out.append(f'bserve_requests_total{{method="{_escape(method)}",status="{status}"}} {count}')
            out += [
                "# HELP bserve_response_bytes_total Body bytes sent in responses.",
                "# TYPE bserve_response_bytes_total counter",
                f"bserve_response_bytes_total {self.bytes_sent}",
                "# HELP bserve_connections_total TCP connections accepted.",
                "# TYPE bserve_connections_total counter",
                f"bserve_connections_total {self.connections_total}",
                "# HELP bserve_connections_open TCP connections currently open.",
                "# TYPE bserve_connections_open gauge",
                f"bserve_connections_open {self.connections_open}",
                "# HELP bserve_protocol_errors_total Connections closed with GOAWAY because the peer broke SPEC.md.",
                "# TYPE bserve_protocol_errors_total counter",
                f"bserve_protocol_errors_total {self.protocol_errors}",
                "# HELP bserve_request_duration_seconds Time from reading a request to sending the last frame.",
                "# TYPE bserve_request_duration_seconds histogram",
            ]
            for bound, count in zip(DURATION_BUCKETS, self.buckets):
                out.append(f'bserve_request_duration_seconds_bucket{{le="{bound}"}} {count}')
            out += [
                f'bserve_request_duration_seconds_bucket{{le="+Inf"}} {self.duration_count}',
                f"bserve_request_duration_seconds_sum {self.duration_sum}",
                f"bserve_request_duration_seconds_count {self.duration_count}",
            ]
        return "\n".join(out) + "\n"


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, metrics):
        self.metrics = metrics
        self.ready = threading.Event()
        super().__init__(address, AdminHandler)


class AdminHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/metrics":
            self.reply(200, self.server.metrics.render(), "text/plain; version=0.0.4")
        elif path == "/healthz":
            self.reply(200, "ok\n")
        elif path == "/readyz":
            if self.server.ready.is_set():
                self.reply(200, "ready\n")
            else:
                self.reply(503, "draining\n")
        elif path == "/version":
            self.reply(200, self.server.metrics.version + "\n")
        else:
            self.reply(404, "not found\n")

    def reply(self, status, text, content_type="text/plain; charset=utf-8"):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass
