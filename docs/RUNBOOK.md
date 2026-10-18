# Runbook

What "healthy" means for bserve, how to tell, and what to do when an alert fires.

## Healthy

| Signal | Healthy | Where |
|---|---|---|
| Ready pods | 2 of 2 | `make status`, alert `BserveNoReadyPods` |
| Synthetic user journey | `bserve-smoke` CronJob succeeds every 2 minutes | `kubectl -n bhttp get jobs`, alert `BserveSmokeCheckFailing` |
| Availability SLO | 99.5% of requests are not 5xx (4xx are the client's fault) | Grafana "bserve", alert `BserveErrorBudgetBurn` |
| Latency SLO | p95 below 100 ms for files in `www/` | Grafana "bserve", alert `BserveSlowResponses` |
| One version running | a single `version` in `bserve_build_info` | Grafana "Running version", alert `BserveMixedVersions` |

The burn-rate alert fires when the 5xx ratio is 10 times the 0.5% budget (5%) for 5 minutes: at that rate a 30-day budget is gone in 3 days, which is worth waking up for. Slow responses only open a ticket.

## Finding one request

Every request is one JSON log line with `request_id`, `status`, `duration_ms` and `version`. Send a request with a known id and find it:

```
./bcurl -v --request-id me localhost:9000/hello.txt      # the response shows x-version and x-served-by (the pod)
kubectl -n bhttp logs -l app.kubernetes.io/component=server --prefix | grep '"request_id": "me-1"'
```

## High error rate

1. `kubectl -n bhttp logs -l app.kubernetes.io/component=server --prefix | grep '"level": "error"'`: the `detail` field holds the exception.
2. If it started with a deploy (`helm -n bhttp history bserve`, Grafana "Running version"), roll back: `make rollback`.
3. Verify: the 5xx panel drops to zero and `make smoke` succeeds.

## Slow responses

1. Check CPU throttling: `kubectl -n bhttp top pods` against the 500m limit.
2. Check open connections on the dashboard: a client holding many connections ties up threads.
3. Raise `replicaCount` or the CPU limit in `values.yaml` and redeploy.

## No ready pods

1. `kubectl -n bhttp describe pods -l app.kubernetes.io/component=server`: look at events (`ImagePullBackOff`, `CrashLoopBackOff`, failed readiness).
2. `ErrImagePull` means the tag is not in the cluster: `make deploy` loads it.
3. A crash shows its traceback in `kubectl -n bhttp logs --previous <pod>`.

## Smoke check failing

1. `kubectl -n bhttp logs job/<latest bserve-smoke job>` shows bcurl's error and exit code (1 connection, 2 protocol, 4/5 status).
2. Exit 1 with pods ready points at the Service or the NetworkPolicy: `kubectl -n bhttp get endpoints bserve`.

## Stuck rollout

1. `kubectl -n bhttp rollout status deployment/bserve` and `kubectl -n bhttp get rs`.
2. The old ReplicaSet keeps serving because `maxUnavailable` is 0, so users are not affected yet.
3. `make rollback`, then find out why the new pods never became ready.
