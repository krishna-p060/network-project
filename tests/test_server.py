"""bserve, driven over a raw socket with hand-built bytes."""

import os
import shutil
import socket
import tempfile
import unittest

from helpers import (
    DATA,
    END_STREAM,
    GOAWAY,
    HEADERS,
    SPEC_REQUEST,
    WWW,
    ServerProcess,
    block,
    frame,
    recv_frame,
    recv_response,
    request,
)

INDEX = b"<h1>Hi</h1>\n"
BINARY = bytes(range(256)) * 200  # 51,200 bytes: three full 16 KiB DATA frames and a 2 KiB one


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        root = os.path.join(cls.tmp, "www")
        os.makedirs(os.path.join(root, "docs"))
        files = {
            "www/index.html": INDEX,
            "www/data.bin": BINARY,
            "www/empty.txt": b"",
            "www/docs/index.html": b"docs\n",
            "secret.txt": b"outside the root\n",
        }
        for name, content in files.items():
            with open(os.path.join(cls.tmp, name), "wb") as f:
                f.write(content)
        os.symlink(os.path.join(cls.tmp, "secret.txt"), os.path.join(root, "link.txt"))
        cls.server = ServerProcess(root)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.sock = self.server.connect()

    def tearDown(self):
        self.sock.close()

    def get(self, path, stream_id=1):
        self.sock.sendall(request(path, stream_id))
        return recv_response(self.sock)

    def test_spec_request_gets_spec_response(self):
        self.sock.sendall(SPEC_REQUEST)
        head = recv_frame(self.sock)
        self.assertEqual(head.raw[:8], bytes.fromhex("00 00 00 44 01 00 00 01"))
        self.assertEqual(
            head.payload[:39],
            bytes.fromhex(
                "03 00 03 32 30 30 "  # :status "200"
                "05 00 09 74 65 78 74 2f 68 74 6d 6c "  # content-type "text/html"
                "06 00 02 31 32 "  # content-length "12"
                "09 00 0a 62 73 65 72 76 65 2f 31 2e 30 "  # server "bserve/1.0"
                "0a 00 1d"  # date, 29 bytes
            ),
        )
        self.assertRegex(head.payload[39:].decode(), r"^\w{3}, \d\d \w{3} \d{4} \d\d:\d\d:\d\d GMT$")
        body = recv_frame(self.sock)
        self.assertEqual(body.raw, bytes.fromhex("00 00 00 0c 02 01 00 01") + INDEX)

    def test_missing_file_is_404(self):
        response = self.get("/nope.html")
        self.assertEqual((response.status, response.body), (404, b"404 Not Found\n"))
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")

    def test_malformed_block_is_400_and_connection_survives(self):
        self.sock.sendall(frame(HEADERS, END_STREAM, 1, bytes.fromhex("0b 00 01 41")))
        response = recv_response(self.sock)
        self.assertEqual((response.status, response.frames[0].stream), (400, 1))
        self.assertEqual(self.get("/index.html", stream_id=2).body, INDEX)

    def test_bad_requests_are_400(self):
        cases = {
            "missing :path": block((":method", "GET")),
            "missing :method": block((":path", "/index.html")),
            "repeated :path": block((":method", "GET"), (":path", "/a"), (":path", "/b")),
            "response pseudo-header": block((":method", "GET"), (":path", "/"), (":status", "200")),
            "relative :path": block((":method", "GET"), (":path", "index.html")),
            "truncated entry": bytes.fromhex("01 00 05 47 45 54"),
        }
        for stream_id, (label, payload) in enumerate(cases.items(), start=1):
            with self.subTest(label):
                self.sock.sendall(frame(HEADERS, END_STREAM, stream_id, payload))
                response = recv_response(self.sock)
                self.assertEqual((response.status, response.frames[-1].stream), (400, stream_id))

    def test_headers_on_stream_zero_is_400(self):
        response = self.get("/index.html", stream_id=0)
        self.assertEqual((response.status, response.frames[0].stream), (400, 0))

    def test_method_other_than_get_is_405(self):
        self.sock.sendall(request("/index.html", method="POST"))
        self.assertEqual(recv_response(self.sock).status, 405)

    def test_paths_outside_the_root_are_404(self):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/docs/../../secret.txt", "/link.txt"):
            with self.subTest(path):
                response = self.get(path)
                self.assertEqual(response.status, 404)
                self.assertNotIn(b"outside", response.body)

    def test_unknown_frame_type_is_skipped(self):
        self.sock.sendall(frame(0x7F, 0xFF, 1, b"future") + request("/index.html"))
        response = recv_response(self.sock)
        self.assertEqual((response.status, response.body), (200, INDEX))

    def test_client_data_frames_are_discarded(self):
        self.sock.sendall(frame(DATA, END_STREAM, 1, b"ignored") + request("/index.html", stream_id=2))
        response = recv_response(self.sock)
        self.assertEqual((response.status, response.frames[0].stream), (200, 2))

    def test_many_requests_on_one_connection(self):
        for stream_id in range(1, 6):
            response = self.get("/index.html", stream_id)
            self.assertEqual(response.body, INDEX)
            self.assertEqual({f.stream for f in response.frames}, {stream_id})

    def test_oversized_frame_gets_400_then_goaway_then_close(self):
        self.sock.sendall(bytes.fromhex("00 10 00 01 01 01 00 07"))
        response = recv_response(self.sock)
        self.assertEqual((response.status, response.frames[0].stream), (400, 7))
        goaway = recv_frame(self.sock)
        self.assertEqual((goaway.type, goaway.stream), (GOAWAY, 0))
        self.assertEqual(self.sock.recv(1), b"")

    def test_truncated_frame_closes_quietly(self):
        self.sock.sendall(SPEC_REQUEST[:10])
        self.sock.shutdown(socket.SHUT_WR)
        self.assertEqual(self.sock.recv(1), b"")

    def test_binary_file_is_byte_exact_in_16k_frames(self):
        response = self.get("/data.bin")
        self.assertEqual(response.body, BINARY)
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertEqual(response.headers["content-length"], str(len(BINARY)))
        data = [f for f in response.frames if f.type == DATA]
        self.assertEqual([len(f.payload) for f in data], [16384, 16384, 16384, 2048])
        self.assertEqual([f.flags & END_STREAM for f in data], [0, 0, 0, END_STREAM])

    def test_empty_file_ends_on_headers(self):
        response = self.get("/empty.txt")
        self.assertEqual(len(response.frames), 1)
        self.assertEqual(response.frames[0].flags, END_STREAM)
        self.assertEqual(response.headers["content-length"], "0")

    def test_directory_serves_its_index(self):
        for path, body in (("/", INDEX), ("/docs", b"docs\n"), ("/docs/", b"docs\n")):
            with self.subTest(path):
                self.assertEqual(self.get(path).body, body)

    def test_query_string_is_ignored(self):
        self.assertEqual(self.get("/index.html?x=1").body, INDEX)


class InjectUnknownTest(unittest.TestCase):
    def test_unknown_frame_comes_before_every_response(self):
        server = ServerProcess(WWW, "--inject-unknown")
        self.addCleanup(server.stop)
        with server.connect() as sock:
            for stream_id in (1, 2):
                sock.sendall(request("/index.html", stream_id))
                response = recv_response(sock)
                self.assertEqual(response.frames[0][:4], (0x7F, 0, stream_id, b"future"))
                self.assertEqual(response.body, INDEX)


if __name__ == "__main__":
    unittest.main()
