"""bcurl against a fake server that replays hand-built bytes, then against the real bserve."""

import os
import re
import socket
import threading
import unittest

from helpers import (
    DATA,
    END_STREAM,
    GOAWAY,
    HEADERS,
    WWW,
    ServerProcess,
    block,
    frame,
    recv_frame,
    response,
    run_bcurl,
)

BODY = b"<h1>Hi</h1>\n"


class FakeServer:
    """Answers each request on the first connection with the next canned reply.

    After the last reply it waits for the client to hang up, unless hang_up is
    set, in which case it closes the connection itself.
    """

    def __init__(self, *replies, hang_up=False):
        self.replies = replies
        self.hang_up = hang_up
        self.requests = []
        self.connections = 0
        self.stopped = False
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def url(self, path="/index.html"):
        return f"127.0.0.1:{self.port}{path}"

    def run(self):
        while not self.stopped:
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            with conn:
                conn.settimeout(15)
                try:
                    if self.connections == 1:
                        for reply in self.replies:
                            self.requests.append(recv_frame(conn))
                            conn.sendall(reply)
                    while not self.hang_up and conn.recv(4096):
                        pass
                except (OSError, EOFError):
                    pass

    def close(self):
        self.stopped = True
        self.thread.join(timeout=5)
        self.listener.close()


class ClientTest(unittest.TestCase):
    def serve(self, *replies, hang_up=False):
        server = FakeServer(*replies, hang_up=hang_up)
        self.addCleanup(server.close)
        return server

    def test_body_goes_to_stdout(self):
        server = self.serve(response(200, BODY, content_type="text/html"))
        result = run_bcurl(server.url())
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, BODY, b""))

    def test_request_bytes(self):
        server = self.serve(response(200, BODY))
        run_bcurl(server.url())
        expected = frame(
            HEADERS,
            END_STREAM,
            1,
            block(
                (":method", "GET"),
                (":path", "/index.html"),
                ("host", f"127.0.0.1:{server.port}"),
                ("user-agent", "bcurl/1.0"),
                ("accept", "*/*"),
            ),
        )
        self.assertEqual(server.requests[0].raw, expected)

    def test_unknown_frame_types_are_skipped(self):
        head = frame(HEADERS, 0, 1, block((":status", "200"), ("content-length", "12")))
        body = frame(DATA, END_STREAM, 1, BODY)
        server = self.serve(frame(0x7F, END_STREAM, 1, b"future") + head + frame(0x42, 0, 0) + body)
        result = run_bcurl(server.url())
        self.assertEqual((result.returncode, result.stdout), (0, BODY))

    def test_exit_status_follows_the_worst_response(self):
        cases = [((200,), 0), ((404,), 4), ((500,), 5), ((200, 404), 4), ((404, 500, 200), 5)]
        for statuses, expected in cases:
            with self.subTest(statuses=statuses):
                bodies = [f"{status}\n".encode() for status in statuses]
                replies = [response(s, b, stream_id=i) for i, (s, b) in enumerate(zip(statuses, bodies), start=1)]
                server = self.serve(*replies)
                result = run_bcurl(*(server.url(f"/{i}") for i in range(len(statuses))))
                self.assertEqual((result.returncode, result.stdout), (expected, b"".join(bodies)))

    def test_all_urls_share_one_connection(self):
        server = self.serve(
            response(200, b"one\n", stream_id=1),
            response(200, b"two\n", stream_id=2),
            response(200, b"three\n", stream_id=3),
        )
        result = run_bcurl(server.url("/1"), server.url("/2"), server.url("/3"))
        self.assertEqual((result.returncode, result.stdout), (0, b"one\ntwo\nthree\n"))
        self.assertEqual(server.connections, 1)
        self.assertEqual([r.stream for r in server.requests], [1, 2, 3])

    def test_urls_for_different_servers_are_refused(self):
        result = run_bcurl("127.0.0.1:9001/a", "127.0.0.1:9002/b")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"never opens a second connection", result.stderr)

    def test_connection_refused_is_exit_1(self):
        with socket.create_server(("127.0.0.1", 0)) as unused:
            port = unused.getsockname()[1]
        result = run_bcurl(f"127.0.0.1:{port}/index.html")
        self.assertEqual(result.returncode, 1)

    def test_protocol_errors_are_exit_2(self):
        head = block((":status", "200"), ("content-length", "12"))
        cases = {
            "wrong stream": response(200, BODY, stream_id=2),
            "DATA before HEADERS": frame(DATA, END_STREAM, 1, BODY),
            "second HEADERS": frame(HEADERS, 0, 1, head) + frame(HEADERS, END_STREAM, 1, head),
            "missing :status": frame(HEADERS, END_STREAM, 1, block(("content-length", "0"))),
            "four-digit :status": frame(HEADERS, END_STREAM, 1, block((":status", "2000"))),
            "malformed block": frame(HEADERS, END_STREAM, 1, bytes.fromhex("0b 00 01 41")),
            "body shorter than content-length": frame(HEADERS, 0, 1, head) + frame(DATA, END_STREAM, 1, b"short"),
            "closed before END_STREAM": frame(HEADERS, 0, 1, head),
            "GOAWAY": frame(GOAWAY, 0, 0, b"shutting down"),
            "frame over 1 MiB": bytes.fromhex("00 10 00 01 02 00 00 01"),
        }
        for label, reply in cases.items():
            with self.subTest(label):
                server = self.serve(reply, hang_up=True)
                result = run_bcurl(server.url())
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_verbose_hexdumps_every_frame(self):
        server = self.serve(frame(0x7F, 0, 1, b"future") + response(200, BODY, content_type="text/html"))
        result = run_bcurl("-v", server.url())
        self.assertEqual((result.returncode, result.stdout), (0, BODY))
        err = result.stderr.decode()
        for line in (
            "> HEADERS len=",
            ">   :path: /index.html",
            "< UNKNOWN(0x7f) len=6 flags=0x00 stream=1  (unknown type, skipped)",
            "<   00000000  00 00 00 06 7f 00 00 01  66 75 74 75 72 65",
            "<   :status: 200",
            "< DATA len=12 flags=END_STREAM stream=1",
            "<   00000000  00 00 00 0c 02 01 00 01  3c 68 31 3e 48 69 3c 2f  |........<h1>Hi</|",
        ):
            self.assertIn(line, err)
        summaries = re.findall(r"^[<>] [A-Z]", err, re.MULTILINE)
        self.assertEqual(len(summaries), 4, err)


class AgainstBserve(unittest.TestCase):
    """Runs ./bserve and ./bcurl through their #! lines, the way the assignment does."""

    def read(self, name):
        with open(os.path.join(WWW, name), "rb") as f:
            return f.read()

    def test_three_urls_one_connection(self):
        server = ServerProcess(WWW, direct=True)
        self.addCleanup(server.stop)
        base = f"localhost:{server.port}"
        result = run_bcurl(f"{base}/index.html", f"{base}/hello.txt", f"{base}/missing.html", direct=True)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.stdout, self.read("index.html") + self.read("hello.txt") + b"404 Not Found\n")
        log = server.wait_for_log(3)
        self.assertEqual(len({line.split()[0] for line in log}), 1, log)

    def test_frames_injected_by_bserve_are_skipped(self):
        server = ServerProcess(WWW, "--inject-unknown", direct=True)
        self.addCleanup(server.stop)
        result = run_bcurl("-v", f"localhost:{server.port}/index.html", direct=True)
        self.assertEqual((result.returncode, result.stdout), (0, self.read("index.html")))
        self.assertIn(b"(unknown type, skipped)", result.stderr)


if __name__ == "__main__":
    unittest.main()
