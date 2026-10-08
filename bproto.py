"""BHTTP/1 wire format: frames and header blocks, as defined in SPEC.md."""

import re
import struct
from dataclasses import dataclass

HEADER = struct.Struct(">IBBH")  # Length, Type, Flags, Stream ID
HEADER_LEN = HEADER.size

HEADERS = 0x01
DATA = 0x02
GOAWAY = 0x03
TYPE_NAMES = {HEADERS: "HEADERS", DATA: "DATA", GOAWAY: "GOAWAY"}

END_STREAM = 0x01

MAX_PAYLOAD = 1 << 20
DATA_CHUNK = 16 * 1024

STATIC_TABLE = (
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
STATIC_INDEX = {name: index for index, name in enumerate(STATIC_TABLE, start=1)}

LITERAL_NAME = re.compile(rb"[a-z0-9-]{1,255}")


class ProtocolError(Exception):
    """The peer sent bytes that break SPEC.md."""


class Truncated(ProtocolError):
    """The connection closed in the middle of a frame."""


class FrameTooLarge(ProtocolError):
    def __init__(self, length, stream_id):
        super().__init__(f"frame length {length} exceeds the {MAX_PAYLOAD}-byte limit")
        self.length = length
        self.stream_id = stream_id


class MalformedHeaders(ProtocolError):
    """A HEADERS payload that cannot be decoded."""


@dataclass(frozen=True)
class Frame:
    type: int
    flags: int
    stream_id: int
    payload: bytes = b""

    @property
    def end_stream(self):
        return self.type in (HEADERS, DATA) and bool(self.flags & END_STREAM)


def encode_frame(frame):
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError(f"payload of {len(frame.payload)} bytes exceeds {MAX_PAYLOAD}")
    return HEADER.pack(len(frame.payload), frame.type, frame.flags, frame.stream_id) + frame.payload


def read_exact(sock, n):
    """Read exactly n bytes; TCP may deliver them in any number of pieces."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise Truncated("connection closed in the middle of a frame")
        buf += chunk
    return bytes(buf)


def read_frame(sock):
    """Read one frame, or return None if the peer closed cleanly between frames."""
    first = sock.recv(HEADER_LEN)
    if not first:
        return None
    header = first + read_exact(sock, HEADER_LEN - len(first))
    length, ftype, flags, stream_id = HEADER.unpack(header)
    if length > MAX_PAYLOAD:
        raise FrameTooLarge(length, stream_id)
    return Frame(ftype, flags, stream_id, read_exact(sock, length))


def encode_headers(headers):
    """Encode (name, value) pairs as a header block."""
    out = bytearray()
    for name, value in headers:
        index = STATIC_INDEX.get(name)
        if index:
            out.append(index)
        else:
            raw_name = name.encode("ascii")
            if not LITERAL_NAME.fullmatch(raw_name):
                raise ValueError(f"invalid literal header name {name!r}")
            out += bytes((0x00, len(raw_name))) + raw_name
        raw_value = value.encode("utf-8")
        if len(raw_value) > 0xFFFF:
            raise ValueError(f"value of {name!r} is longer than 65535 bytes")
        out += len(raw_value).to_bytes(2, "big") + raw_value
    return bytes(out)


def decode_headers(payload):
    """Decode a header block into a list of (name, value) pairs."""
    headers = []
    pos = 0
    while pos < len(payload):
        ref = payload[pos]
        pos += 1
        if 1 <= ref <= len(STATIC_TABLE):
            name = STATIC_TABLE[ref - 1]
        elif ref == 0x00:
            if pos >= len(payload):
                raise MalformedHeaders("literal name length is missing")
            size = payload[pos]
            raw_name = payload[pos + 1 : pos + 1 + size]
            pos += 1 + size
            if len(raw_name) < size:
                raise MalformedHeaders("literal name is truncated")
            if not LITERAL_NAME.fullmatch(raw_name):
                raise MalformedHeaders(f"invalid literal header name {raw_name!r}")
            name = raw_name.decode("ascii")
        else:
            raise MalformedHeaders(f"reserved static table index 0x{ref:02x}")
        if pos + 2 > len(payload):
            raise MalformedHeaders(f"value length of {name} is missing")
        size = int.from_bytes(payload[pos : pos + 2], "big")
        raw_value = payload[pos + 2 : pos + 2 + size]
        pos += 2 + size
        if len(raw_value) < size:
            raise MalformedHeaders(f"value of {name} is truncated")
        try:
            value = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            raise MalformedHeaders(f"value of {name} is not UTF-8") from None
        headers.append((name, value))
    return headers


def hexdump(data, prefix=""):
    """Format bytes like `hexdump -C`: offset, 16 hex bytes, printable ASCII."""
    lines = []
    for offset in range(0, len(data), 16):
        row = data[offset : offset + 16]
        hex_part = f"{row[:8].hex(' '):<23}  {row[8:].hex(' ')}"
        text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row)
        lines.append(f"{prefix}{offset:08x}  {hex_part:<48}  |{text}|")
    return "\n".join(lines)


def describe_frame(frame):
    """One-line summary such as 'HEADERS len=55 flags=END_STREAM stream=1'."""
    size = len(frame.payload)
    name = TYPE_NAMES.get(frame.type)
    if name is None:
        return f"UNKNOWN(0x{frame.type:02x}) len={size} flags=0x{frame.flags:02x} stream={frame.stream_id}"
    flags = ["END_STREAM"] if frame.end_stream else []
    other = frame.flags & ~END_STREAM if frame.type in (HEADERS, DATA) else frame.flags
    if other:
        flags.append(f"0x{other:02x}")
    return f"{name} len={size} flags={'|'.join(flags) or '-'} stream={frame.stream_id}"
