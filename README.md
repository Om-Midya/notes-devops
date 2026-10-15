# Notes

A small personal notes app. It is a Flask web page and a JSON API that store notes in Postgres.

## Run

```sh
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/notes
python app.py
```

Open http://localhost:5000.

## API

- `GET /api/notes` lists notes.
- `POST /api/notes` with `{"body": "text"}` adds a note.
- `DELETE /api/notes/<id>` deletes a note.
