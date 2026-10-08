# BHTTP/1: HTTP in binary frames

BHTTP/1 carries HTTP-style `GET` requests and their responses over one TCP connection, as length-prefixed binary frames instead of text lines. (It is a course protocol, unrelated to the RFC 9292 message format.) MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Connection

A client opens one TCP connection and sends any number of requests over it, one at a time: it sends a request, reads the complete response, and only then sends the next. Neither side closes the connection after a response; the client closes it when it has no more requests. There is no handshake, so the first bytes in each direction are a frame. All integers are unsigned and big-endian (network byte order).

## 2. Frame header

Every frame is an 8-byte header followed by exactly Length bytes of payload.

```
 0               1               2               3
+---------------+---------------+---------------+---------------+
|                        Length (32 bits)                       |
+---------------+---------------+---------------+---------------+
|   Type (8)    |   Flags (8)   |        Stream ID (16)         |
+---------------+---------------+---------------+---------------+
|                     Payload (Length bytes) ...                |
```

- **Length**: payload size in bytes, not counting the header. It MUST NOT exceed 1,048,576 (1 MiB) for any frame type, known or unknown.
- **Type**: how to read the payload (section 3).
- **Flags**: yes/no bits whose meaning depends on the type. Bits a type does not define MUST be sent as 0 and ignored on receipt.
- **Stream ID**: the request a frame belongs to. A client numbers its requests 1, 2, 3, ... on each connection (at most 65,535), and every frame of a response carries its request's number. Stream 0 means the connection as a whole.

**Why these widths.** HTTP/2 chose 24/8/8/31 because it multiplexes many concurrent streams on one connection. A 24-bit length caps a frame at 16 MiB (the default limit is only 16 KiB), so one frame cannot hold up the others for long. Eight bits of type leave room for over 200 future frame types. Flags only need meaning per type, so 8 bits is plenty. Streams are never reused, so a long-lived connection needs a large ID space: 31 bits, with the top bit reserved (which also suits languages without unsigned 32-bit integers).

BHTTP/1 runs one request at a time, so it trades differently. A 32-bit length keeps the header word-aligned and trivial to parse; the explicit 1 MiB cap stops a peer from making the receiver buffer gigabytes, and bodies larger than a frame are split across `DATA` frames anyway. Type and flags stay at 8 bits for HTTP/2's reasons. A 16-bit stream ID is more than enough for sequential requests, and keeping it in version 1 means a version 2 can interleave requests without changing the header.

## 3. Frame types

| Type | Name | Payload | Flags |
|---|---|---|---|
| `0x01` | `HEADERS` | a header block (section 4) | `0x01` `END_STREAM` |
| `0x02` | `DATA` | body bytes | `0x01` `END_STREAM` |
| `0x03` | `GOAWAY` | UTF-8 reason text; always on stream 0 | none |

`END_STREAM` marks the last frame of a request or a response. The sender of `GOAWAY` closes the connection straight after it; a receiver MUST stop sending and close as well.

**Unknown types.** A receiver that meets a frame type it does not know MUST read and discard exactly Length payload bytes and carry on with the next frame, whatever the frame's flags or stream ID. It MUST NOT treat the frame as an error. This is how a version 2 can add frame types without breaking version 1 peers; a version 2 feature must therefore be safe to ignore, or wait until the peer has shown, with a frame of its own, that it understands it.

## 4. Header blocks

A `HEADERS` payload is a list of entries that ends where the payload ends. Each entry is a name, then a value:

```
name   1 byte    0x01-0x0A   index into the static table
                 0x00        literal: 1-byte length N (1-255), then N name bytes
value  2 bytes   length M (0-65,535), then M bytes of UTF-8
```

| Index | Name | Index | Name |
|---|---|---|---|
| `0x01` | `:method` | `0x06` | `content-length` |
| `0x02` | `:path` | `0x07` | `user-agent` |
| `0x03` | `:status` | `0x08` | `accept` |
| `0x04` | `host` | `0x09` | `server` |
| `0x05` | `content-type` | `0x0A` | `date` |

These are the ten names BHTTP/1 peers actually send: clients send `:method`, `:path`, `host`, `user-agent` and `accept`; servers send `:status`, `content-type`, `content-length`, `server` and `date`. Senders SHOULD use the index for any name in the table and a literal for anything else. A literal name MUST be lowercase ASCII letters, digits and `-`, so pseudo-headers (names starting with `:`) can only be sent by index. Name bytes `0x0B`-`0xFF` are reserved; a block containing one is malformed.

This is the first two mechanisms of HPACK (RFC 7541): a static table and length-prefixed literals. HPACK's dynamic table and Huffman coding are deliberately left out.

## 5. Requests and responses

**Request.** One `HEADERS` frame on the next stream ID, with `END_STREAM` set (version 1 has no request bodies). It MUST contain exactly one `:method` and one `:path` and no other pseudo-headers, and `:path` MUST start with `/`.

**Response.** A server answers every `HEADERS` frame it receives with exactly one response on the same stream ID, without waiting for `END_STREAM`. The response is a `HEADERS` frame holding exactly one `:status` (three ASCII digits), `content-type`, `content-length` (the body size in decimal), `server` and `date` (HTTP date format, as in the example below), followed by the body in `DATA` frames. A server SHOULD keep `DATA` payloads to 16 KiB; receivers MUST accept any size up to the frame limit. The last frame of the response carries `END_STREAM`: the `HEADERS` frame if the body is empty, otherwise the last `DATA` frame. The `DATA` payloads MUST add up to `content-length`. A server discards any `DATA` frames a client sends.

**Paths.** The server drops any `?query`, percent-decodes the rest, and serves the regular file it names under its root directory; a path naming a directory serves that directory's `index.html`. After symbolic links are resolved, the file MUST still be inside the root.

## 6. Errors

| Server receives | Server sends |
|---|---|
| A `HEADERS` frame on stream 0, or one whose block is malformed (reserved index, truncated entry, bad literal name, value not UTF-8), lacks or repeats `:method` or `:path`, adds another pseudo-header, or has a `:path` not starting with `/` | 400 on that stream; the connection stays open |
| A `:method` other than `GET` | 405 |
| A path that does not name a regular file inside the root (including `..` and symlink escapes) | 404 |
| Any other failure before the response has started | 500 |
| A Length above 1 MiB | 400 on that frame's stream ID, then `GOAWAY`, then close |
| End of connection in the middle of a frame | Nothing; close |

Error responses carry a short `text/plain` body. A client MUST treat each of these as a protocol error and close the connection: a frame above 1 MiB; a `HEADERS` or `DATA` frame for a stream other than its current request; `DATA` before `HEADERS`; a second `HEADERS` frame in one response; a missing, repeated or malformed `:status`; a body whose size differs from `content-length`; and the connection ending before `END_STREAM`.

## 7. Example

`GET /index.html` on stream 1, 63 bytes in all. Values are shown as text; [HEXDUMP.md](HEXDUMP.md) annotates every byte of a real capture.

```
00 00 00 37   Length 55
01            Type HEADERS
01            Flags END_STREAM
00 01         Stream 1
01  00 03  "GET"              :method
02  00 0b  "/index.html"      :path
04  00 0e  "localhost:9000"   host
07  00 09  "bcurl/1.0"        user-agent
08  00 03  "*/*"              accept
```

The response: a `HEADERS` frame with a 68-byte block, then one `DATA` frame carrying the 12-byte body.

```
00 00 00 44  01  00  00 01    Length 68, HEADERS, no flags, stream 1
03  00 03  "200"                             :status
05  00 09  "text/html"                       content-type
06  00 02  "12"                              content-length
09  00 0a  "bserve/1.0"                      server
0a  00 1d  "Thu, 08 Oct 2026 13:00:00 GMT"   date
00 00 00 0c  02  01  00 01    Length 12, DATA, END_STREAM, stream 1
3c 68 31 3e 48 69 3c 2f 68 31 3e 0a          "<h1>Hi</h1>\n"
```
