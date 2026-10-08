# Annotated hexdump: one complete request and response

Captured on 8 October 2026 with:

```
$ ./bserve ./www 9000 --bind 127.0.0.1
$ ./bcurl -v localhost:9000/index.html
```

`--bind 127.0.0.1` only keeps the server off the local network; the bytes are the same without it. The exchange is one request (63 bytes, client to server) and one response (96 bytes, server to client) over a single TCP connection.

## 1. The `-v` trace

`bcurl -v` writes this to stderr. `>` marks frames bcurl sent and `<` frames it received. Each frame gets a summary line, its decoded headers, and a hexdump of the exact bytes on the wire, header included. The body, `<h1>Hi</h1>`, went to stdout, and the exit status was 0.

```
* connected to localhost:9000 (127.0.0.1)
> HEADERS len=55 flags=END_STREAM stream=1
>   :method: GET
>   :path: /index.html
>   host: localhost:9000
>   user-agent: bcurl/1.0
>   accept: */*
>   00000000  00 00 00 37 01 01 00 01  01 00 03 47 45 54 02 00  |...7.......GET..|
>   00000010  0b 2f 69 6e 64 65 78 2e  68 74 6d 6c 04 00 0e 6c  |./index.html...l|
>   00000020  6f 63 61 6c 68 6f 73 74  3a 39 30 30 30 07 00 09  |ocalhost:9000...|
>   00000030  62 63 75 72 6c 2f 31 2e  30 08 00 03 2a 2f 2a     |bcurl/1.0...*/*|
< HEADERS len=68 flags=- stream=1
<   :status: 200
<   content-type: text/html
<   content-length: 12
<   server: bserve/1.0
<   date: Thu, 08 Oct 2026 13:19:46 GMT
<   00000000  00 00 00 44 01 00 00 01  03 00 03 32 30 30 05 00  |...D.......200..|
<   00000010  09 74 65 78 74 2f 68 74  6d 6c 06 00 02 31 32 09  |.text/html...12.|
<   00000020  00 0a 62 73 65 72 76 65  2f 31 2e 30 0a 00 1d 54  |..bserve/1.0...T|
<   00000030  68 75 2c 20 30 38 20 4f  63 74 20 32 30 32 36 20  |hu, 08 Oct 2026 |
<   00000040  31 33 3a 31 39 3a 34 36  20 47 4d 54              |13:19:46 GMT|
< DATA len=12 flags=END_STREAM stream=1
<   00000000  00 00 00 0c 02 01 00 01  3c 68 31 3e 48 69 3c 2f  |........<h1>Hi</|
<   00000010  68 31 3e 0a                                       |h1>.|
* connection closed
```

The server logged the request as `127.0.0.1:52092 stream=1 GET /index.html 200 (12 bytes)`.

## 2. Request: `HEADERS`, client to server, 63 bytes

Offsets are from the start of the frame.

```
offset  bytes                                         meaning
0x00    00 00 00 37                                   Length = 0x37 = 55 payload bytes
0x04    01                                            Type = 0x01 HEADERS
0x05    01                                            Flags = 0x01 END_STREAM: the request has no body
0x06    00 01                                         Stream ID = 1, the first request on this connection
        ---- header block, 55 bytes ----
0x08    01                                            name: static index 1 = :method
0x09    00 03                                         value length = 3
0x0b    47 45 54                                      "GET"
0x0e    02                                            name: static index 2 = :path
0x0f    00 0b                                         value length = 11
0x11    2f 69 6e 64 65 78 2e 68 74 6d 6c              "/index.html"
0x1c    04                                            name: static index 4 = host
0x1d    00 0e                                         value length = 14
0x1f    6c 6f 63 61 6c 68 6f 73 74 3a 39 30 30 30     "localhost:9000"
0x2d    07                                            name: static index 7 = user-agent
0x2e    00 09                                         value length = 9
0x30    62 63 75 72 6c 2f 31 2e 30                    "bcurl/1.0"
0x39    08                                            name: static index 8 = accept
0x3a    00 03                                         value length = 3
0x3c    2a 2f 2a                                      "*/*"
0x3f    end of frame: 8 header bytes + 55 payload bytes = 63
```

These bytes are identical to the example in SPEC.md section 7 and to the hand-typed test vector in `tests/helpers.py`.

## 3. Response, frame 1: `HEADERS`, server to client, 76 bytes

```
offset  bytes                                         meaning
0x00    00 00 00 44                                   Length = 0x44 = 68 payload bytes
0x04    01                                            Type = 0x01 HEADERS
0x05    00                                            Flags = none: DATA frames follow
0x06    00 01                                         Stream ID = 1, copied from the request
        ---- header block, 68 bytes ----
0x08    03                                            name: static index 3 = :status
0x09    00 03                                         value length = 3
0x0b    32 30 30                                      "200"
0x0e    05                                            name: static index 5 = content-type
0x0f    00 09                                         value length = 9
0x11    74 65 78 74 2f 68 74 6d 6c                    "text/html"
0x1a    06                                            name: static index 6 = content-length
0x1b    00 02                                         value length = 2
0x1d    31 32                                         "12": the body is 12 bytes
0x1f    09                                            name: static index 9 = server
0x20    00 0a                                         value length = 10
0x22    62 73 65 72 76 65 2f 31 2e 30                 "bserve/1.0"
0x2c    0a                                            name: static index 10 = date
0x2d    00 1d                                         value length = 29
0x2f    54 68 75 2c 20 30 38 20 4f 63                 "Thu, 08 Oc"
0x39    74 20 32 30 32 36 20 31 33 3a                 "t 2026 13:"
0x43    31 39 3a 34 36 20 47 4d 54                    "19:46 GMT"
0x4c    end of frame: 8 header bytes + 68 payload bytes = 76
```

## 4. Response, frame 2: `DATA`, server to client, 20 bytes

```
offset  bytes                                         meaning
0x00    00 00 00 0c                                   Length = 0x0c = 12 payload bytes
0x04    02                                            Type = 0x02 DATA
0x05    01                                            Flags = 0x01 END_STREAM: last frame of the response
0x06    00 01                                         Stream ID = 1
0x08    3c 68 31 3e 48 69 3c 2f 68 31 3e 0a           "<h1>Hi</h1>\n", all 12 bytes of www/index.html
0x14    end of frame: 8 header bytes + 12 payload bytes = 20
```

The `DATA` payload adds up to 12 bytes, matching `content-length`. `END_STREAM` tells bcurl the response is complete without the server closing the connection. bcurl had no more URLs, so it closed the connection itself.

## 5. Skipping a frame type the client does not know

Same request, with the server started as `./bserve ./www 9000 --bind 127.0.0.1 --inject-unknown`. The server now sends a frame of type `0x7f` before the response. Version 1 defines no such type, so it stands in for a version 2 extension. The new lines in the `-v` trace:

```
< UNKNOWN(0x7f) len=6 flags=0x00 stream=1  (unknown type, skipped)
<   00000000  00 00 00 06 7f 00 00 01  66 75 74 75 72 65        |........future|
```

```
offset  bytes                                         meaning
0x00    00 00 00 06                                   Length = 6 payload bytes
0x04    7f                                            Type = 0x7f, not defined in version 1
0x05    00                                            Flags = none
0x06    00 01                                         Stream ID = 1
0x08    66 75 74 75 72 65                             "future": payload that bcurl reads and discards
0x0e    end of frame: 8 header bytes + 6 payload bytes = 14
```

bcurl reads Length from the header, discards exactly 6 bytes, and carries on with the next frame. The `HEADERS` and `DATA` frames that follow are the same as in sections 3 and 4, stdout still gets `<h1>Hi</h1>`, and the exit status is still 0. This is the rule in SPEC.md section 3 that unknown frame types MUST be skipped cleanly.
