# Notes

A small personal notes app. It is a Flask web page and a JSON API that store notes in Postgres. The baseline is a plain script. This repository turns it into a system that you can build, deploy, operate, and observe on a local Kubernetes cluster.

| | |
|---|---|
| Baseline commit | [`830aa5b`](https://github.com/Om-Midya/notes-devops/commit/830aa5b5159b40b1d370004c76c75067cf339fa3), the app before any DevOps work |
| Pull request | [#1](https://github.com/Om-Midya/notes-devops/pull/1) contains all improvements |
| Design decisions | [docs/DECISIONS.md](docs/DECISIONS.md) |
| Runbook and SLOs | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| Evidence | [docs/EVIDENCE.md](docs/EVIDENCE.md) |

## Prerequisites

You need macOS or Linux with these tools. The versions are the ones that were tested.

- Docker (28.5) with at least 6 GB of memory for the Docker VM
- kind (0.33), kubectl, Helm (4.3), git, curl, openssl

On macOS: `brew install kind helm kubectl`.

## Deploy

```sh
git clone https://github.com/Om-Midya/notes-devops.git && cd notes-devops
./deploy.sh            # deploy the current commit
./deploy.sh <git-sha>  # deploy, or roll back to, a specific commit
```

`deploy.sh` does these steps:

1. If the kind cluster `notes` does not exist, it creates it. Every command uses `--context kind-notes`, so your other clusters are not touched.
2. It installs metrics-server and kube-prometheus-stack once.
3. It creates the `notes` namespace with the restricted Pod Security Standard and generates a random DB password into a Secret.
4. It uses the CI image `ghcr.io/om-midya/notes-devops:<sha>`. If CI did not publish that commit, the script builds it locally. A local build needs a clean working tree.
5. It runs `helm upgrade --install --rollback-on-failure` and then `helm test`, and prints the running version.

The first run takes about 5 minutes, because it downloads the monitoring stack. Later runs take about 1 minute.

## Access

| What | How |
|---|---|
| App | http://localhost:8080 (web page), `curl localhost:8080/api/notes`, `curl localhost:8080/version` |
| Grafana | `kubectl --context kind-notes -n monitoring port-forward svc/monitoring-grafana 3000:80`, then http://localhost:3000, dashboard "Notes app". User `admin`. Password: `kubectl --context kind-notes -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' \| base64 -d` |
| Prometheus | `kubectl --context kind-notes -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090`, then http://localhost:9090/alerts |
| Jaeger | `kubectl --context kind-notes -n notes port-forward svc/notes-jaeger 16686`, then http://localhost:16686, service `notes` |
| Logs | `kubectl --context kind-notes -n notes logs -l app.kubernetes.io/name=notes-app` (JSON, one line per request, with `trace_id`) |

API: `GET /api/notes`, `POST /api/notes` with `{"body": "text"}`, `DELETE /api/notes/<id>`. Operations endpoints: `/healthz`, `/readyz`, `/version`, `/metrics`.

## Pipeline

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and every push to `main`.

1. `test`: ruff (with bandit security rules), pytest against a real Postgres, pip-audit.
2. `security`: gitleaks over the full git history, Trivy misconfiguration scan of the Dockerfile and the rendered Helm chart.
3. `build-and-deploy-test`: builds one amd64 and arm64 image tagged with the commit SHA and pushes it to GHCR. Then it scans the image with Grype and deploys it on a fresh kind cluster. It runs `helm test` and makes sure that `/version` returns the commit SHA.
4. `release`: waits for manual approval on the `release` environment, then points `:stable` at the tested digest.

To deploy a released commit locally, run `./deploy.sh <sha>`.

## Run the app without Kubernetes

```sh
docker run -d --name notes-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notes -p 5432:5432 postgres:17-alpine
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/notes .venv/bin/pytest -q
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/notes .venv/bin/python app.py   # http://localhost:5050
```

## Repository layout

| Path | Purpose |
|---|---|
| `app.py`, `test_app.py` | The app (with metrics, logs, traces, and probes) and its tests |
| `Dockerfile` | Non-root image, version baked in from the commit SHA |
| `chart/notes/` | Helm chart: app, Postgres StatefulSet, Jaeger, NetworkPolicies, HPA, PDB, ServiceMonitor, SLO alerts, Grafana dashboard, smoke test |
| `deploy.sh`, `kind-config.yaml`, `monitoring-values.yaml` | Local cluster and repeatable deployment |
| `.github/` | CI/CD workflow and Dependabot |
| `docs/` | Decisions, runbook, evidence |

## Known limitations

- The cluster is one kind node on a laptop. There is no high availability for nodes, and topology spread has no effect.
- Postgres is a single pod with no backups and no replication. Losing the kind node loses the data.
- GitHub cannot reach the laptop cluster, so the last deployment step is the manual `./deploy.sh <sha>`. CI proves the same chart and image on its own kind cluster.
- Jaeger keeps traces in memory, and Prometheus keeps 2 days of data. Logs are read with `kubectl logs`, because there is no log store such as Loki.
- Alertmanager receives alerts but has no receiver (email or chat) configured.
- The app has no user accounts. Anyone who can reach port 8080 can read and write notes.
