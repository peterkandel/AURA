# ADHAYAN deployment

## Runtime architecture

The Flask runtime serves public pages from structured JSON in `content/`. The administrator uses `/admin` to edit homepage/site copy, About content, News, Opportunities, Founders, and National Heads through server-rendered forms and lists; the interface does not ask the administrator to edit JSON. Admin edits are validated and committed to the configured GitHub repository through the Contents API. The web service does not use PostgreSQL or SQLite for runtime content persistence. Existing Alembic migrations and local database files are retained as legacy data and are not loaded by the application.

## Local development

1. Create and activate `.venv`.
2. Install the existing dependency set with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set a random `SECRET_KEY`.
4. Public pages work from checked-in `content/` files without GitHub credentials.
5. To exercise admin writes, set an administrator password and a GitHub token with repository content write access, plus owner, repository, branch, and content root.
6. Run the app with `python app.py`; run tests with `.venv/Scripts/python.exe -m pytest -q` on Windows or `.venv/bin/python -m pytest -q` on Unix.

## Required runtime configuration

Set these variables in Render's environment settings. Do not put credentials in source control.

- `APP_ENV=production`
- `SECRET_KEY=<long random signing secret>`
- `ADMIN_USERNAME=admin`
- `ADMIN_PASSWORD=<shared administrator password>`
- `ADMIN_SESSION_LIFETIME_SECONDS=1800`
- `GITHUB_TOKEN=<server-side token with contents write access>`
- `GITHUB_OWNER=<repository owner>`
- `GITHUB_REPO=<repository name>`
- `GITHUB_BRANCH=<branch to update>`
- `GITHUB_CONTENT_ROOT=content`
- `SESSION_COOKIE_SECURE=true`
- `SESSION_COOKIE_SAMESITE=Lax`
- `FLASK_DEBUG=0`

Production startup fails if the admin password or required GitHub persistence credentials are missing. The GitHub token is used only by the server-side GitHub Contents API client. GitHub writes use the existing file SHA for updates and deletes; conflicts and API/network failures are shown as failed saves, not success. Avoid a token with broader repository permissions than content editing requires.

## Render commands

The runtime no longer needs `flask db upgrade`. Use the existing build installation step and start with:

```text
pip install -r requirements.txt
```

```text
gunicorn app:app
```

The default Render filesystem is ephemeral, which is why the CMS commits canonical content to GitHub rather than writing production JSON or SQLite files locally. JSON is the human-readable content format, not a mutable production filesystem store. A SQLite file would require a Render persistent disk, which is a paid, single-instance runtime attachment and complicates deploys; managed PostgreSQL is deliberately not being used for this file-backed editorial workload. Public reads use the configured repository through the GitHub API when owner/repository/branch settings are present; otherwise the checked-in `content/` files provide a read-only preview/fallback mode.

GitHub API reads happen on public requests when remote content is configured, so token/repository availability and API rate limits are runtime dependencies. Admin writes create commits on the configured branch; if Render auto-deploys from that branch, a content edit may also trigger a deploy, but the running app reads canonical content through GitHub. The admin login attempt limiter is process-local and resets on process restart; it is a modest guard for a single-worker service, not a distributed rate-limiting system.

## CMS content documents

- `content/site.json` contains organization identity, homepage copy and ordered sections, navigation labels, journey steps, and the selected featured-news IDs.
- `content/about.json` contains About page title/introduction, ordered custom sections, and the Founders/National Heads headings and empty-state copy.
- `content/news.json` contains news records with slug, title, summary/body, category/date/byline, optional HTTPS image, publication state, ordering, and legacy featured metadata.
- `content/opportunities.json` contains opportunity records with slug, title/description, category, topics, difficulty, weekly hours, skills, optional image, publication/open/closed/featured states, and ordering.
- `content/people/founders.json` and `content/people/national_heads.json` contain active/inactive ordered people; National Head records also store country.

Homepage featured news is selected with published-article checkboxes in both the News manager and Homepage/Site settings. An explicit selection, including none selected, becomes authoritative for the homepage. The read-only repository audit confirmed that no `content/` tree or content JSON paths exist on remote `main`, and local News/Opportunity collections were empty. The canonical paths are therefore `news.json` and `opportunities.json`; no existing remote content files were renamed or overwritten.

Admin credentials are `ADMIN_USERNAME` (default `admin`) and `ADMIN_PASSWORD`; successful sign-in creates a signed session that expires after `ADMIN_SESSION_LIFETIME_SECONDS`. All CMS mutations and logout use CSRF-protected POST forms. Long-form homepage/About sections are added and edited as individual section forms with numeric ordering. Rich-text editing, image uploads, scheduling, admin roles, and a major visual redesign are intentionally deferred.

## Legacy data and dependencies

The SQLAlchemy models and the migrations for users, opportunities, and opportunity interests are no longer imported by the runtime. Existing migrations are intentionally unchanged. Local ignored files under `instance/` have not been deleted or transformed; review and back up any data before a later retirement/migration decision.

`requirements.txt` is intentionally unchanged during this foundation step. Flask-SQLAlchemy, Flask-Migrate, Flask-Login, Psycopg, and database-only utility dependencies can be removed in a later cleanup after confirming no external deployment command depends on the old migration workflow. Flask, Flask-WTF, Gunicorn, pytest, and python-dotenv remain useful; GitHub integration uses Python's standard library and adds no package.