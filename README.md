# BHTTP/1: HTTP in binary frames

A binary version of HTTP, built as an individual course project: a two-page spec, a file server (`bserve`) and a client (`bcurl`) that talk to each other only through the bytes the spec defines.

| File | What it is |
|---|---|
| [SPEC.md](SPEC.md) | The protocol (hand-in 1) |
| `bserve`, `bcurl`, `bproto.py` | The programs (hand-in 2) |
| [HEXDUMP.md](HEXDUMP.md) | One complete request and response, annotated byte by byte (hand-in 3) |
| `www/` | Sample files to serve |
| `tests/` | The tests |
| `bmetrics.py` | Prometheus metrics and health endpoints for bserve |
| `Dockerfile`, `charts/`, `deploy/`, `Makefile`, `.github/` | Container, Helm chart, cluster setup and CI/CD |
| [docs/](docs/) | Design decisions and runbook |

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

## Running it on Kubernetes

bserve runs on a local [kind](https://kind.sigs.k8s.io) cluster, deployed with Helm, built and checked by GitHub Actions, and watched by Prometheus and Grafana. The reasoning behind each choice is in [docs/DECISIONS.md](docs/DECISIONS.md); what healthy means and how to respond to alerts is in [docs/RUNBOOK.md](docs/RUNBOOK.md).

### Prerequisites

Docker (Docker Desktop on macOS), `kind` 0.24 or newer, `kubectl`, `helm` 3 or newer, `make`, Python 3.8 or newer. On macOS: `brew install kind kubectl helm`.

### Steps

```
make test          # unit and integration tests
make cluster       # kind cluster "bhttp" (1 control plane, 2 workers) and namespace bhttp
make monitoring    # optional: Prometheus, Grafana and Alertmanager (about 5 minutes)
make deploy        # image for the current commit, loaded into kind, helm upgrade --install
make smoke         # bcurl through localhost:9000, then finds that request in the pod logs
make status        # helm history, pods, services, policies
```

`make deploy` pulls `ghcr.io/krishna-p060/network-project:<commit>` if CI has published it and builds it locally otherwise. Run `make monitoring` before `make deploy` so the ServiceMonitor and alert rules are installed; without it they are skipped.

### Access

| What | How |
|---|---|
| bserve | `./bcurl -v localhost:9000/` (kind maps NodePort 30900 to `127.0.0.1:9000`) |
| Metrics of one pod | `kubectl -n bhttp port-forward deploy/bserve 9100` then `curl localhost:9100/metrics` |
| Grafana | `make grafana`, open http://localhost:3000, dashboard "bserve"; the command prints the generated password |
| Prometheus | `make prometheus`, open http://localhost:9090, then the Alerts tab |

### Releasing and rolling back

Every pull request is tested, scanned, built and deployed to a throwaway kind cluster by [.github/workflows/ci.yml](.github/workflows/ci.yml). After a merge to `main`, or a manual run, the tested image is pushed to GHCR, tagged with its commit, behind the `release` environment's approval. `make deploy` rolls it out with no drop in capacity; `make rollback` returns to the previous Helm revision.

To check which version answered a request, look at the `x-version` and `x-served-by` headers in `./bcurl -v`.

### Configuration

The container sets these; locally all are off, and bserve behaves exactly as described above.

| Variable | Meaning |
|---|---|
| `BSERVE_VERSION` | Version reported in metrics and logs; when set, also sent as `x-version` and `x-served-by` headers |
| `BSERVE_METRICS_PORT` | Serve `/metrics`, `/healthz`, `/readyz` and `/version` over HTTP on this port |
| `BSERVE_LOG_FORMAT` | `json` for one JSON object per request |
| `BSERVE_DRAIN_SECONDS` | On SIGTERM, stay up and not-ready this long before exiting |

`bcurl --request-id ID` sends `x-request-id: ID-<stream>`, which bserve echoes back and logs.

### Known limitations

- The local cluster is not reachable from GitHub, so the last step to it is `make deploy` rather than automatic.
- No distributed tracing; the request id links a client request to its log line.
- Load balancing is per TCP connection, as for any Service carrying a non-HTTP protocol.

## Checking the spec without a partner

The assignment is written for pairs, where the spec is the only thing the server's author and the client's author share. Done alone, both programs share `bproto.py`, so a mistake in it could look correct from both sides. These checks stand in for the second person:

- **Golden bytes.** The SPEC.md example was worked out by hand, and `tests/helpers.py` holds those bytes typed in by hand. The encoder has to reproduce them exactly, and the real capture in HEXDUMP.md matches them.
- **Independent test code.** The frame builders and readers in `tests/helpers.py` follow SPEC.md directly and do not import `bproto`. The server is tested without the client, and the client without the server.
- **Unknown frames in both directions.** The server tests send unknown frame types to `bserve`; `bserve --inject-unknown` and the fake server send them to `bcurl`.

The strongest check left is to run `bcurl` against another student's server, or their client against `bserve`, using nothing but SPEC.md.
