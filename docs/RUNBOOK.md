# Runbook

All commands use the `kind-notes` context. Set this alias first:

```sh
alias k='kubectl --context kind-notes -n notes'
```

## What healthy means

| Signal | Healthy value | Where to look |
|---|---|---|
| Ready app pods | 2 or more | `k get pods`, Grafana panel "Ready app pods" |
| Error ratio on user routes | under 1% (SLO: 99% success) | Prometheus `notes:error_ratio:rate5m` |
| p95 latency | under 300 ms | Grafana panel "p95 latency by route" |
| Running version | one SHA on every pod | `curl localhost:8080/version`, Grafana panel "Running version per pod" |
| User journey | `helm test` prints `smoke ok` | `helm --kube-context kind-notes test notes -n notes --logs` |

## Alerts

### NotesHighErrorRate

More than 1% of user requests return 5xx for 2 minutes.

1. Find the failing route: in Prometheus, run `sum by (route, status) (rate(http_requests_total{status=~"5.."}[5m]))`.
2. Read the errors: `k logs -l app.kubernetes.io/name=notes-app --tail=100 | grep '"status": 5'`. Copy a `trace_id`.
3. Open that trace in Jaeger (`k port-forward svc/notes-jaeger 16686`, then http://localhost:16686/trace/<trace_id>). A failed `SELECT` or `INSERT` span points to the database. A fast failure with no DB span points to the app code.
4. If the errors started with a release, compare the version on the dashboard with the last good SHA. Roll back with `./deploy.sh <last-good-sha>`.
5. If Postgres is the cause, go to NotesNoReadyPods step 2.

### NotesNoReadyPods

Prometheus cannot scrape any app pod for 1 minute. Users get connection errors.

1. `k get pods`. If app pods are `Running` but `0/1` ready, readiness fails, which usually means Postgres is down.
2. `k get pod notes-postgres-0` and `k logs notes-postgres-0`. If the pod is gone, make sure that the StatefulSet has `replicas: 1` (`k get sts`). If the PVC is full, `k exec notes-postgres-0 -- df -h /var/lib/postgresql/data`.
3. If app pods are in `CrashLoopBackOff`, read `k logs <pod> --previous` and roll back to the last good SHA.

### NotesHighLatency

p95 latency is above 300 ms for 5 minutes.

1. Look at CPU: `k top pods`. If the pods are near the 500m limit, make sure that the HPA scales (`k get hpa`).
2. Open a slow trace in Jaeger and find the slowest span. A slow SQL span means the database is the limit.

## Verify recovery

These three conditions together mean that recovery is complete:

1. `k get pods` shows every pod ready.
2. `helm --kube-context kind-notes test notes -n notes --logs` prints `smoke ok`.
3. `notes:error_ratio:rate5m` returns to 0 and the alert state in Prometheus (http://localhost:9090/alerts) is no longer `firing`.

## Access

```sh
kubectl --context kind-notes -n monitoring port-forward svc/monitoring-grafana 3000:80        # Grafana, user admin
kubectl --context kind-notes -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d
kubectl --context kind-notes -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090  # Prometheus
kubectl --context kind-notes -n notes port-forward svc/notes-jaeger 16686                    # Jaeger
```
