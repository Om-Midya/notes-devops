# Runs against a real Postgres (DATABASE_URL), the same as CI.
from app import app

c = app.test_client()
assert c.get("/readyz").status_code == 200  # creates the schema, as the readiness probe does


def test_note_lifecycle():
    r = c.post("/api/notes", json={"body": "buy milk"})
    assert r.status_code == 201
    nid = r.json["id"]
    assert any(n["id"] == nid for n in c.get("/api/notes").json)
    assert c.delete(f"/api/notes/{nid}").status_code == 204
    assert c.delete(f"/api/notes/{nid}").status_code == 404


def test_validation():
    assert c.post("/api/notes", json={"body": "  "}).status_code == 400
    assert c.post("/api/notes", json={"body": "x" * 1001}).status_code == 400


def test_form_rejects_cross_site():
    assert c.post("/notes", data={"body": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/notes", data={"body": "x"}, headers={"Origin": "http://localhost"}).status_code == 302


def test_health_and_metrics():
    assert c.get("/healthz").status_code == 200
    assert c.get("/readyz").status_code == 200
    assert c.get("/version").json == {"version": "dev"}
    m = c.get("/metrics").text
    assert 'notes_build_info{version="dev"} 1.0' in m
    assert 'http_requests_total{method="GET",route="/healthz",status="200"}' in m
