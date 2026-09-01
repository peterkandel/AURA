# AURA deployment preparation

## Local development

1. Create and activate `.venv`.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set a random development `SECRET_KEY`.
4. Keep `APP_ENV=development`, then run `python app.py`.
5. Run tests with `.venv/Scripts/python.exe -m pytest -q` on Windows or `.venv/bin/python -m pytest -q` on Unix.

Development may use SQLite and HTTP. The development app creates missing tables for convenience; production does not.

## Production configuration

Set these environment variables through the hosting platform, never in source control:

- `APP_ENV=production`
- `SECRET_KEY=<long random value>`
- `DATABASE_URL=postgresql://...`
- `SESSION_COOKIE_SECURE=true`
- `SESSION_COOKIE_SAMESITE=Lax`
- `SESSION_LIFETIME_SECONDS=3600`
- `FLASK_DEBUG=0`

Production refuses to start without a strong secret or a non-SQLite database URL. Debug mode is disabled.

## Database migrations

The initial migration is in `migrations/versions`. For a fresh database:

```text
flask --app app db upgrade
```

For future schema changes:

```text
flask --app app db migrate -m "describe change"
flask --app app db upgrade
```

Do not use `db.create_all()` for production schema management. Existing local SQLite data is not deleted by this setup.

## Recommended hosting architecture

Run the Flask application behind HTTPS and a reverse proxy using Gunicorn and PostgreSQL:

```text
gunicorn --workers 2 --bind 0.0.0.0:8000 app:app
```

The current limiter is intentionally simple and process-local for development. Multiple production workers require a shared rate-limit store such as Redis, or a production limiter configuration backed by the hosting platform, before relying on rate limiting across workers.