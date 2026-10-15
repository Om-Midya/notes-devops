import json
import logging
import os
import time

import psycopg
from flask import Flask, g, jsonify, redirect, request
from markupsafe import escape
from opentelemetry import trace
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

VERSION = os.environ.get("APP_VERSION", "dev")
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/notes")

# Tracing: export spans only when an OTLP endpoint is configured (Jaeger in the cluster).
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "notes", "service.version": VERSION}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app, excluded_urls="healthz,readyz,metrics")
PsycopgInstrumentor().instrument()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        ctx = trace.get_current_span().get_span_context()
        line = {"ts": self.formatTime(record), "level": record.levelname, "msg": record.getMessage(), "version": VERSION}
        if ctx.is_valid:
            line["trace_id"] = format(ctx.trace_id, "032x")
        return json.dumps(line | getattr(record, "extra", {}))


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
log = logging.getLogger("notes")
log.addHandler(handler)
log.setLevel(logging.INFO)

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["route"])
Gauge("notes_build_info", "Running app version", ["version"]).labels(VERSION).set(1)


def db():
    return psycopg.connect(DB, autocommit=True, connect_timeout=3)


@app.before_request
def start_timer():
    g.start = time.perf_counter()


@app.after_request
def record(resp):
    route = request.url_rule.rule if request.url_rule else "unmatched"
    elapsed = time.perf_counter() - g.start
    REQUESTS.labels(request.method, route, resp.status_code).inc()
    LATENCY.labels(route).observe(elapsed)
    if route not in ("/healthz", "/readyz", "/metrics"):
        log.info(
            "request",
            extra={"extra": {"method": request.method, "route": route, "status": resp.status_code, "ms": round(elapsed * 1000, 1)}},
        )
    return resp


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/readyz")
def readyz():
    # Ready means Postgres answers and the schema exists, so the pod gets traffic only then.
    # Postgres can start after the app; the kubelet keeps probing, so no startup retry loop is needed.
    try:
        with suppress_instrumentation(), db() as c:  # no trace per probe
            c.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, body TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now())")
        return "ready"
    except psycopg.Error as e:
        log.warning("not ready", extra={"extra": {"error": str(e)}})
        return "database unavailable", 503


@app.get("/version")
def version():
    return jsonify(version=VERSION)


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def all_notes():
    with db() as c:
        rows = c.execute("SELECT id, body, created_at FROM notes ORDER BY id DESC").fetchall()
    return [{"id": r[0], "body": r[1], "created_at": r[2].isoformat()} for r in rows]


@app.get("/")
def index():
    items = "".join(f"<li>{escape(n['body'])}</li>" for n in all_notes())
    return f"""<!doctype html><title>Notes</title><h1>Notes</h1><small>version {escape(VERSION)}</small>
<form method=post action=/notes><input name=body required maxlength=1000><button>Add</button></form><ul>{items}</ul>"""


@app.post("/notes")
def add_form():
    # CSRF guard: browsers send Origin on cross-site form posts; reject foreign ones.
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] != request.host:
        return "cross-site request rejected", 403
    body = request.form.get("body", "").strip()[:1000]
    if body:
        with db() as c:
            c.execute("INSERT INTO notes (body) VALUES (%s)", (body,))
    return redirect("/")


@app.get("/api/notes")
def list_notes():
    return jsonify(all_notes())


@app.post("/api/notes")
def add_note():
    body = (request.get_json(silent=True) or {}).get("body", "").strip()
    if not body or len(body) > 1000:
        return jsonify(error="body is required, max 1000 characters"), 400
    with db() as c:
        nid = c.execute("INSERT INTO notes (body) VALUES (%s) RETURNING id", (body,)).fetchone()[0]
    return jsonify(id=nid, body=body), 201


@app.delete("/api/notes/<int:nid>")
def delete_note(nid):
    with db() as c:
        deleted = c.execute("DELETE FROM notes WHERE id = %s", (nid,)).rowcount
    return ("", 204) if deleted else (jsonify(error="not found"), 404)


if __name__ == "__main__":
    app.run(port=5050)
