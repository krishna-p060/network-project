"""bproto against the example bytes in SPEC.md section 7 and other hand-typed bytes."""

import unittest

# helpers puts the project root on sys.path, so it has to be imported before bproto.
from helpers import SPEC_REQUEST, SPEC_RESPONSE

import bproto
from bproto import DATA, END_STREAM, HEADERS, Frame

REQUEST_HEADERS = [
    (":method", "GET"),
    (":path", "/index.html"),
    ("host", "localhost:9000"),
    ("user-agent", "bcurl/1.0"),
    ("accept", "*/*"),
]
RESPONSE_HEADERS = [
    (":status", "200"),
    ("content-type", "text/html"),
    ("content-length", "12"),
    ("server", "bserve/1.0"),
    ("date", "Thu, 08 Oct 2026 13:00:00 GMT"),
]
BODY = b"<h1>Hi</h1>\n"


class FakeSocket:
    """Hands out its data a few bytes per recv() call, like a slow TCP connection."""

    def __init__(self, data, step=3):
        self.data = data
        self.step = step

    def recv(self, n):
        size = min(n, self.step)
        chunk, self.data = self.data[:size], self.data[size:]
        return chunk


class SpecExample(unittest.TestCase):
    def test_request_encodes_to_spec_bytes(self):
        frame = Frame(HEADERS, END_STREAM, 1, bproto.encode_headers(REQUEST_HEADERS))
        self.assertEqual(bproto.encode_frame(frame), SPEC_REQUEST)

    def test_response_encodes_to_spec_bytes(self):
        head = Frame(HEADERS, 0, 1, bproto.encode_headers(RESPONSE_HEADERS))
        body = Frame(DATA, END_STREAM, 1, BODY)
        self.assertEqual(bproto.encode_frame(head) + bproto.encode_frame(body), SPEC_RESPONSE)

    def test_spec_bytes_decode(self):
        sock = FakeSocket(SPEC_REQUEST + SPEC_RESPONSE)
        request = bproto.read_frame(sock)
        self.assertEqual((request.type, request.flags, request.stream_id), (HEADERS, END_STREAM, 1))
        self.assertEqual(bproto.decode_headers(request.payload), REQUEST_HEADERS)
        head = bproto.read_frame(sock)
        self.assertEqual(bproto.decode_headers(head.payload), RESPONSE_HEADERS)
        self.assertFalse(head.end_stream)
        body = bproto.read_frame(sock)
        self.assertEqual((body.payload, body.end_stream), (BODY, True))
        self.assertIsNone(bproto.read_frame(sock))


class ReadFrame(unittest.TestCase):
    def test_one_byte_at_a_time(self):
        frame = bproto.read_frame(FakeSocket(SPEC_REQUEST, step=1))
        self.assertEqual(bproto.encode_frame(frame), SPEC_REQUEST)

    def test_eof_inside_header(self):
        with self.assertRaises(bproto.Truncated):
            bproto.read_frame(FakeSocket(SPEC_REQUEST[:5]))

    def test_eof_inside_payload(self):
        with self.assertRaises(bproto.Truncated):
            bproto.read_frame(FakeSocket(SPEC_REQUEST[:20]))

    def test_length_over_limit(self):
        with self.assertRaises(bproto.FrameTooLarge) as caught:
            bproto.read_frame(FakeSocket(bytes.fromhex("00 10 00 01 01 01 00 07")))
        self.assertEqual(caught.exception.stream_id, 7)

    def test_length_at_limit(self):
        raw = bytes.fromhex("00 10 00 00 02 00 00 01") + bytes(bproto.MAX_PAYLOAD)
        frame = bproto.read_frame(FakeSocket(raw, step=65536))
        self.assertEqual(len(frame.payload), bproto.MAX_PAYLOAD)

    def test_unknown_type_comes_back_whole(self):
        sock = FakeSocket(bytes.fromhex("00 00 00 06 7f 01 00 01") + b"future" + SPEC_REQUEST)
        unknown = bproto.read_frame(sock)
        self.assertEqual((unknown.type, unknown.payload, unknown.end_stream), (0x7F, b"future", False))
        self.assertEqual(bproto.encode_frame(bproto.read_frame(sock)), SPEC_REQUEST)


class HeaderBlock(unittest.TestCase):
    def test_literal_name(self):
        raw = bytes.fromhex("00 08 78 2d 63 6f 75 72 73 65 00 03 73 73 74")  # x-course: sst
        self.assertEqual(bproto.encode_headers([("x-course", "sst")]), raw)
        self.assertEqual(bproto.decode_headers(raw), [("x-course", "sst")])

    def test_empty_value_and_empty_block(self):
        self.assertEqual(bproto.decode_headers(bytes.fromhex("04 00 00")), [("host", "")])
        self.assertEqual(bproto.decode_headers(b""), [])

    def test_utf8_value(self):
        raw = bytes.fromhex("04 00 05 63 61 66 c3 a9")  # host: café
        self.assertEqual(bproto.decode_headers(raw), [("host", "café")])
        self.assertEqual(bproto.encode_headers([("host", "café")]), raw)

    def test_malformed_blocks(self):
        cases = {
            "reserved index": "0b 00 01 41",
            "missing value length": "01",
            "value cut short": "01 00 05 47 45 54",
            "literal length missing": "00",
            "literal name cut short": "00 05 61 62",
            "empty literal name": "00 00 00 00",
            "uppercase literal name": "00 01 41 00 00",
            "pseudo-header as literal": "00 05 3a 70 61 74 68 00 01 2f",
            "value not UTF-8": "04 00 01 ff",
        }
        for label, hex_block in cases.items():
            with self.subTest(label), self.assertRaises(bproto.MalformedHeaders):
                bproto.decode_headers(bytes.fromhex(hex_block))

    def test_encoder_rejects_bad_names(self):
        for name in ("X-Upper", ":custom", "", "a" * 256):
            with self.subTest(name=name), self.assertRaises(ValueError):
                bproto.encode_headers([(name, "v")])


class Output(unittest.TestCase):
    def test_hexdump_has_hexdump_c_layout(self):
        lines = bproto.hexdump(SPEC_RESPONSE[-20:]).split("\n")
        self.assertEqual(
            lines,
            [
                "00000000  00 00 00 0c 02 01 00 01  3c 68 31 3e 48 69 3c 2f  |........<h1>Hi</|",
                "00000010  68 31 3e 0a" + " " * 39 + "|h1>.|",
            ],
        )

    def test_describe_frame(self):
        cases = [
            (Frame(HEADERS, END_STREAM, 1, bytes(55)), "HEADERS len=55 flags=END_STREAM stream=1"),
            (Frame(DATA, 0, 2), "DATA len=0 flags=- stream=2"),
            (Frame(0x7F, 0x01, 1, b"future"), "UNKNOWN(0x7f) len=6 flags=0x01 stream=1"),
        ]
        for frame, expected in cases:
            with self.subTest(expected):
                self.assertEqual(bproto.describe_frame(frame), expected)


if __name__ == "__main__":
    unittest.main()
