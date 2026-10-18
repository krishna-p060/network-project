"""The operational side of bserve: metrics, health, JSON logs, version headers, SIGTERM."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from helpers import BSERVE, WWW, recv_response, request, run_bcurl


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpsServer:
    def __init__(self, env):
        self.port = free_port()
        self.metrics_port = free_port()
        full_env = {**os.environ, "BSERVE_METRICS_PORT": str(self.metrics_port), **env}
        self.proc = subprocess.Popen(
            [sys.executable, BSERVE, WWW, str(self.port), "--bind", "127.0.0.1"],
            stderr=subprocess.PIPE,
            text=True,
            env=full_env,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.http("/readyz")
                return
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        self.proc.kill()
        raise RuntimeError("bserve did not become ready")

    def http(self, path):
        url = f"http://127.0.0.1:{self.metrics_port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.proc.stderr.close()


class OpsTest(unittest.TestCase):
    def setUp(self):
        self.server = OpsServer({"BSERVE_VERSION": "abc1234", "BSERVE_LOG_FORMAT": "json"})
        self.addCleanup(self.server.stop)

    def test_health_and_version(self):
        self.assertEqual(self.server.http("/healthz"), (200, "ok\n"))
        self.assertEqual(self.server.http("/readyz"), (200, "ready\n"))
        self.assertEqual(self.server.http("/version"), (200, "abc1234\n"))

    def test_metrics_count_requests_by_status(self):
        base = f"127.0.0.1:{self.server.port}"
        run_bcurl(f"{base}/index.html", f"{base}/missing")
        status, text = self.server.http("/metrics")
        self.assertEqual(status, 200)
        self.assertIn('bserve_build_info{version="abc1234"} 1', text)
        self.assertIn('bserve_requests_total{method="GET",status="200"} 1', text)
        self.assertIn('bserve_requests_total{method="GET",status="404"} 1', text)
        self.assertIn("bserve_request_duration_seconds_count 2", text)

    def test_version_and_request_id_reach_the_client(self):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout=5) as sock:
            sock.sendall(request("/hello.txt", 1))
            response = recv_response(sock)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["x-version"], "abc1234")
        self.assertIn("x-served-by", response.headers)

        result = run_bcurl("-v", "--request-id", "trace42", f"127.0.0.1:{self.server.port}/hello.txt")
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"x-request-id: trace42-1", result.stderr)

    def test_json_log_and_sigterm(self):
        run_bcurl("--request-id", "t", f"127.0.0.1:{self.server.port}/hello.txt")
        self.server.proc.send_signal(signal.SIGTERM)
        self.assertEqual(self.server.proc.wait(timeout=5), 0)
        lines = self.server.proc.stderr.read().splitlines()
        records = [json.loads(line) for line in lines if line.startswith("{")]
        self.assertEqual(len(records), 1, lines)
        self.assertEqual(records[0]["status"], 200)
        self.assertEqual(records[0]["request_id"], "t-1")
        self.assertEqual(records[0]["version"], "abc1234")
        self.assertIn("bserve: stopped", lines[-1])


if __name__ == "__main__":
    unittest.main()
