# Evidence

All output below comes from the real system: the GitHub Actions run for commit `acf7897`, the GHCR registry, and the local kind cluster that `./deploy.sh acf7897…` deployed. The laptop clock was 3 days behind during this work. So the timestamps from the cluster and from git show 15 October, and GitHub shows the real date, 18 October 2026.

## 1. Source to registry to runtime: one version everywhere

The tested version is commit `acf78972c7bfd79da8f5435b03c967e7da6fb912`.

| Step | Evidence |
|---|---|
| CI run | [actions/runs/39130647808](https://github.com/Om-Midya/notes-devops/actions/runs/39130647808): `pull_request` event, head `acf7897`. The jobs `test`, `security`, `build-and-deploy-test`, and `release` all succeeded. |
| Release approval | The `release` job waited on the `release` environment until a required reviewer approved it (comment: "Release acf7897: checks, scans and kind deploy test passed"). |
| Image built and tested in CI | `ghcr.io/om-midya/notes-devops:acf7897…`, digest `sha256:2cf79e1bfc0fb6618ef2d969f26d8c56965b8c9324847128456b57250492c3b8`. The CI log shows `smoke ok, version acf78972c7bfd79da8f5435b03c967e7da6fb912`. |
| Released tag | `:stable` points to the same digest `sha256:2cf79e1b…`, without a rebuild. |
| Running pods | Both pods have the label `app.kubernetes.io/version=acf7897…` and `imageID=ghcr.io/om-midya/notes-devops@sha256:2cf79e1b…`. |
| App | `curl localhost:8080/version` returns `{"version":"acf78972c7bfd79da8f5435b03c967e7da6fb912"}`. |
| Metric | `notes_build_info{version="acf7897…"} 1` for each of the 2 pods. |

```text
$ docker buildx imagetools inspect ghcr.io/om-midya/notes-devops:acf78972c7bfd79da8f5435b03c967e7da6fb912
MediaType: application/vnd.oci.image.index.v1+json
Digest:    sha256:2cf79e1bfc0fb6618ef2d969f26d8c56965b8c9324847128456b57250492c3b8
$ docker buildx imagetools inspect ghcr.io/om-midya/notes-devops:stable
Digest:    sha256:2cf79e1bfc0fb6618ef2d969f26d8c56965b8c9324847128456b57250492c3b8

$ kubectl get pods -l app.kubernetes.io/name=notes-app -o jsonpath=...
notes-app-6c8b6df75-9qgtx label-version=acf78972c7bf... imageID=ghcr.io/om-midya/notes-devops@sha256:2cf79e1bfc0f...
notes-app-6c8b6df75-pvwj5 label-version=acf78972c7bf... imageID=ghcr.io/om-midya/notes-devops@sha256:2cf79e1bfc0f...
```

`./deploy.sh acf7897…` output (end):

```text
using CI image ghcr.io/om-midya/notes-devops:acf78972c7bfd79da8f5435b03c967e7da6fb912
STATUS: deployed
REVISION: 7
POD LOGS: notes-smoke (smoke)
smoke ok, version acf78972c7bfd79da8f5435b03c967e7da6fb912
deployed: {"version":"acf78972c7bfd79da8f5435b03c967e7da6fb912"}  ->  http://localhost:8080
```

## 2. A working user journey

```text
$ curl -X POST localhost:8080/api/notes -H 'content-type: application/json' -d '{"body":"Buy groceries for the week"}'
{"body":"Buy groceries for the week","id":6}
$ curl localhost:8080/api/notes
[{"body":"Buy groceries for the week","created_at":"2026-10-15T01:23:37.416836+00:00","id":6},
 {"body":"first note on k8s","created_at":"2026-10-15T00:23:00.892231+00:00","id":2}]
$ curl localhost:8080/        (web page)
version acf78972c7bfd79da8f5435b03c967e7da6fb912
<li>Buy groceries for the week</li>
<li>first note on k8s</li>
$ curl -X POST localhost:8080/api/notes -d '{"body":""}'          -> {"error":"body is required, max 1000 characters"} HTTP 400
$ curl -X POST localhost:8080/notes -H 'Origin: https://evil.example' -> cross-site request rejected HTTP 403
```

The note "first note on k8s" was written by the first deployment. It survived six Helm revisions, one automatic rollback, and a Postgres outage, because it lives on the StatefulSet PVC.

## 3. Logs, traces, and metrics for the same request

The JSON log line has the deployed version and a `trace_id`:

```json
{"ts": "2026-10-15 01:22:47,434", "level": "INFO", "msg": "request", "version": "acf78972c7bfd79da8f5435b03c967e7da6fb912", "trace_id": "eb9646ccd755ab63c0a181ade6d9715f", "method": "POST", "route": "/api/notes", "status": 201, "ms": 7.6}
```

The same `trace_id` in Jaeger (`/api/traces/eb9646cc…`) shows the HTTP span and its SQL child span:

```text
POST /api/notes   8.5 ms  service=notes version=acf7897  201
INSERT            1.7 ms  service=notes version=acf7897  INSERT INTO notes (body) VALUES (%s) RETURNING id
```

Prometheus (scraped through the ServiceMonitor):

```text
sum by (route,status) (rate(http_requests_total[5m]))     {route=/api/notes, status=200} 0.149 req/s, plus 201, 204, 400, 403 series
histogram_quantile(0.95, ... route="/api/notes")          0.0186 s  (SLO: under 0.3 s)
kube_deployment_status_replicas_available{notes-app}       2
PrometheusRule notes-slo: notes:error_ratio:rate5m (recording), NotesHighErrorRate, NotesHighLatency, NotesNoReadyPods: all health=ok
```

The Grafana sidecar provisioned the "Notes app" dashboard. It has six panels: request rate, error ratio, p95 latency, running version per pod, ready pods, and restarts.

## 4. Fault demonstration 1: a bad release rolls back without user impact

Symptom to create: a release with an image tag that does not exist (a typo in the SHA). A request loop ran during the whole upgrade.

Hypothesis: the new pod never becomes ready. Because of `maxUnavailable: 0`, the old pods keep serving. `--rollback-on-failure` returns to the last good revision.

```text
$ helm upgrade notes chart/notes --set image.tag=0000000-typo ... --rollback-on-failure --timeout 60s
Error: UPGRADE FAILED: release notes failed, and has been rolled back due to rollback-on-failure being set:
resource Deployment/notes/notes-app not ready. status: InProgress, message: Updated: 1/2

user requests during the bad release:   330 x HTTP 200 (0 errors)

$ helm history notes
3   superseded  Upgrade complete
4   failed      Upgrade "notes" failed: resource Deployment/notes/notes-app not ready ...
5   deployed    Rollback to 3
$ curl localhost:8080/version  -> {"version":"95dc1e6fa948d1eba965c5f992f19d9fe968189c"}  (the previous good version)
```

Verification: the version is the previous one, all 330 requests succeeded, and Helm records the failed revision and the rollback.

## 5. Fault demonstration 2: database outage, alert, and recovery

Change: `kubectl scale statefulset notes-postgres --replicas=0`.

Hypothesis: `/readyz` fails, so both app pods leave the Service and users cannot connect. Liveness passes, so the app pods do not restart. `NotesNoReadyPods` fires after 1 minute. When Postgres returns with the same PVC, the pods become ready again and no data is lost.

```text
before:  notes stored: 1, no Notes alerts
after 25s:
  notes-app-5658dc5f59-j76rx   0/1   Running   0
  notes-app-5658dc5f59-qqzxp   0/1   Running   0
  user request -> HTTP 000 (connection refused)
  log: {"level": "WARNING", "msg": "not ready", "error": "failed to resolve host 'notes-postgres': [Errno -2] Name or service not known"}
alert:   NotesNoReadyPods pending (01:07:13), then firing; Alertmanager received NotesNoReadyPods (active)

fix at 01:08:58: kubectl scale statefulset notes-postgres --replicas=1
01:09:03  deployment notes-app condition Available met; both app pods 1/1 ready, restarts=0
          notes stored after recovery: 1
01:09:16  alert resolved
```

Verification: the pods became ready 5 seconds after Postgres returned, with no restarts. The data is the same and the alert cleared. One finding came from this test. The first version of the alert used the scrape metric `up`, which stays 1 for unready pods, so it never fired. The alert now uses `kube_deployment_status_replicas_available`.

## 6. Scaling under load

24 parallel request loops ran for 150 seconds:

```text
HPA:     cpu: 861%/70%   min 2  max 5  current 4
events:  New size: 4; reason: cpu resource utilization (percentage of request) above target
         New size: 5; reason: cpu resource utilization (percentage of request) above target
pods:    5 x notes-app 1/1 Running
error ratio during load: 0
p95 during load: 0.215 s (SLO: under 0.3 s)
```

## 7. Security controls

```text
$ kubectl -n notes run bad --image=busybox --privileged --dry-run=server
Error from server (Forbidden): pods "bad" is forbidden: violates PodSecurity "restricted:latest": privileged ...

$ kubectl exec deploy/notes-app -- sh -c 'id; touch /app/x; ls /var/run/secrets/kubernetes.io'
uid=10001 gid=0(root) groups=0(root)
touch: cannot touch '/app/x': Read-only file system
ls: cannot access '/var/run/secrets/kubernetes.io': No such file or directory     (no service account token)

NetworkPolicy, pod without the app label:  notes-postgres.notes.svc:5432 - no response
NetworkPolicy, app pod:                    app -> postgres:5432 connected
```

The CI security checks on the same commit:

- gitleaks found no leaks in the full history.
- The Trivy configuration scan found 0 HIGH or CRITICAL issues in the Dockerfile and the four rendered chart templates.
- The Grype image scan found no fixable HIGH or CRITICAL issues.
- pip-audit found no known vulnerabilities.
- The ruff bandit rules passed.

The only password literal in the repository is `POSTGRES_PASSWORD: postgres` for the temporary Postgres container in the CI `test` job.

Trivy 0.65 reported a false positive for this image: `pcre2 10.32-3.el8_6` (a RHEL package) inside a Debian 13 image. `dpkg` shows `libpcre2-8-0 10.46-1~deb13u3`, which is the fixed version, and Grype agrees. So Grype is the image gate, and Trivy scans only the configuration.

## 8. Problems found and fixed during the work

| Symptom | Evidence | Cause | Fix |
|---|---|---|---|
| First deployment: `/api/notes` returned 500 | Logs: 30 x `failed to resolve host 'notes-postgres'`, then `relation "notes" does not exist` | A headless Service has no DNS record until its pod is ready. The startup retry loop gave up and the app started without a schema. | Schema creation moved into `/readyz`. No pod gets traffic before the schema exists. |
| `helm test` timed out | `URLError: Connection timed out` | The default-deny egress policy blocked the test pod. | A test-only NetworkPolicy for the smoke pod. |
| Image tag did not match the commit | The PR run pushed tag `eb212c5…`, which is not a branch commit | On `pull_request` events, `github.sha` is a temporary merge commit. | The tag uses `github.event.pull_request.head.sha`, and checkout uses the same commit. |
| `kind load` failed for CI images | `ctr: content digest ... not found` | kind cannot import a partial multi-arch index. | The node pulls the public image from GHCR. `kind load` is only for local builds. |
| Database outage alert never fired | `up == 1` during the outage | Unready pods are still scraped. | The alert uses available replicas. |
| Error ratio panel was empty | The recording rule returned no series | A `sum` over no 5xx series is empty, not 0. | `or vector(0)` on the numerator. |

## Limitations of this evidence

- Commits after `acf7897` change only the documentation and the error ratio recording rule. The PR description lists the final commit with its own CI run and deployment check.
- The runtime evidence comes from a single-node kind cluster on a laptop.
- An early run pushed one extra image tag (`eb212c5…`, a GitHub merge commit) before the tag fix. That tag was never deployed.
