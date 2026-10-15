import os

import psycopg
from flask import Flask, jsonify, redirect, request
from markupsafe import escape

app = Flask(__name__)
DB = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/notes")


def db():
    return psycopg.connect(DB, autocommit=True)


with db() as c:
    c.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, body TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now())")


def all_notes():
    with db() as c:
        rows = c.execute("SELECT id, body, created_at FROM notes ORDER BY id DESC").fetchall()
    return [{"id": r[0], "body": r[1], "created_at": r[2].isoformat()} for r in rows]


@app.get("/")
def index():
    items = "".join(f"<li>{escape(n['body'])}</li>" for n in all_notes())
    return f"""<!doctype html><title>Notes</title><h1>Notes</h1>
<form method=post action=/notes><input name=body required><button>Add</button></form><ul>{items}</ul>"""


@app.post("/notes")
def add_form():
    with db() as c:
        c.execute("INSERT INTO notes (body) VALUES (%s)", (request.form["body"],))
    return redirect("/")


@app.get("/api/notes")
def list_notes():
    return jsonify(all_notes())


@app.post("/api/notes")
def add_note():
    body = (request.get_json(silent=True) or {}).get("body", "").strip()
    if not body:
        return jsonify(error="body is required"), 400
    with db() as c:
        nid = c.execute("INSERT INTO notes (body) VALUES (%s) RETURNING id", (body,)).fetchone()[0]
    return jsonify(id=nid, body=body), 201


@app.delete("/api/notes/<int:nid>")
def delete_note(nid):
    with db() as c:
        deleted = c.execute("DELETE FROM notes WHERE id = %s", (nid,)).rowcount
    return ("", 204) if deleted else (jsonify(error="not found"), 404)


if __name__ == "__main__":
    app.run(debug=True)
