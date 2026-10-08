# BHTTP/1: HTTP in binary frames

A binary version of HTTP, built as an individual course project: a two-page spec, a file server (`bserve`) and a client (`bcurl`) that talk to each other only through the bytes the spec defines.

| File | What it is |
|---|---|
| [SPEC.md](SPEC.md) | The protocol (hand-in 1) |
| `bserve`, `bcurl`, `bproto.py` | The programs (hand-in 2) |
| [HEXDUMP.md](HEXDUMP.md) | One complete request and response, annotated byte by byte (hand-in 3) |
| `www/` | Sample files to serve |
| `tests/` | The tests |

Needs Python 3.8 or newer (developed on 3.11) and nothing outside the standard library.

## Running it

In one terminal:

```
./bserve ./www 9000
```

In another:

```
./bcurl -v localhost:9000/index.html
```

### bserve

`./bserve ROOT PORT [--bind ADDR] [--inject-unknown]`

- Serves the files under `ROOT`. `/` and other directories serve their `index.html`. Paths that lead outside `ROOT`, through `..` or a symlink, get a 404.
- Keeps every connection open until the client closes it, with one thread per connection.
- Logs one line per request to stderr.
- `--bind 127.0.0.1` listens on localhost only; the default is all interfaces.
- `--inject-unknown` sends a frame of unknown type `0x7f` before every response, to check that clients skip it.
- Port `0` picks a free port, which the startup line reports.

### bcurl

`./bcurl [-v] host:port/path [host:port/path ...]`

- Fetches the URLs in order over one TCP connection and writes the bodies to stdout byte for byte, so `./bcurl localhost:9000/photo.png > photo.png` works.
- The port defaults to 9000. A scheme prefix such as `http://` is accepted and ignored.
- All URLs must name the same host and port, because bcurl never opens a second connection.
- `-v` prints every frame sent (`>`) and received (`<`) to stderr: a summary line, the decoded headers and a hexdump.

| Exit status | Meaning |
|---|---|
| 0 | Every response was below 400 |
| 1 | Usage error, connection failure, or no answer within 10 seconds |
| 2 | Protocol error: the server broke SPEC.md or sent `GOAWAY` |
| 4 | At least one 4xx response, and no 5xx |
| 5 | At least one 5xx response |

## Tests

```
python3 -m unittest discover tests
```

They take a few seconds:

- `tests/test_vectors.py` checks `bproto.py` against the SPEC.md example bytes, typed in by hand, and covers partial reads, oversized frames and malformed header blocks.
- `tests/test_server.py` talks to a real `bserve` over a raw socket: exact response bytes, 400, 404 and 405, path traversal, unknown frame types, many requests on one connection, the oversized-frame `GOAWAY`, and a 50 KB binary file split into 16 KiB frames.
- `tests/test_client.py` runs `bcurl` against a fake server that replays hand-built bytes: the body on stdout, skipped unknown frames, exit codes, one connection for several URLs, protocol errors and the `-v` output. The last two tests run `./bcurl` against `./bserve` through their `#!` lines.

## Checking the spec without a partner

The assignment is written for pairs, where the spec is the only thing the server's author and the client's author share. Done alone, both programs share `bproto.py`, so a mistake in it could look correct from both sides. These checks stand in for the second person:

- **Golden bytes.** The SPEC.md example was worked out by hand, and `tests/helpers.py` holds those bytes typed in by hand. The encoder has to reproduce them exactly, and the real capture in HEXDUMP.md matches them.
- **Independent test code.** The frame builders and readers in `tests/helpers.py` follow SPEC.md directly and do not import `bproto`. The server is tested without the client, and the client without the server.
- **Unknown frames in both directions.** The server tests send unknown frame types to `bserve`; `bserve --inject-unknown` and the fake server send them to `bcurl`.

The strongest check left is to run `bcurl` against another student's server, or their client against `bserve`, using nothing but SPEC.md.
