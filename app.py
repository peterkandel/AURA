import hmac
import json
import os
import re
import time
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from dotenv import load_dotenv

from content_service import (
    COLLECTIONS,
    ContentError,
    ContentRepository,
    ContentValidationError,
)
from github_persistence import GitHubContentError, GitHubContentStore


load_dotenv()

app = Flask(__name__)
app.config["APP_ENV"] = os.getenv("APP_ENV", "development").lower()
app.config["DEBUG"] = app.config["APP_ENV"] == "development" and os.getenv("FLASK_DEBUG", "0") == "1"
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or (
    "adhyayan-local-dev-secret-key-change-me" if app.config["APP_ENV"] != "production" else ""
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv(
    "SESSION_COOKIE_SECURE", "true" if app.config["APP_ENV"] == "production" else "false"
).lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = int(os.getenv("ADMIN_SESSION_LIFETIME_SECONDS", "1800"))
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["ADMIN_USERNAME"] = os.getenv("ADMIN_USERNAME", "admin")
app.config["ADMIN_PASSWORD"] = os.getenv("ADMIN_PASSWORD", "")
app.config["GITHUB_TOKEN"] = os.getenv("GITHUB_TOKEN", "")
app.config["GITHUB_OWNER"] = os.getenv("GITHUB_OWNER", "")
app.config["GITHUB_REPO"] = os.getenv("GITHUB_REPO", "")
app.config["GITHUB_BRANCH"] = os.getenv("GITHUB_BRANCH", "main")
app.config["GITHUB_CONTENT_ROOT"] = os.getenv("GITHUB_CONTENT_ROOT", "content")
app.config["CONTENT_LOCAL_ROOT"] = Path(__file__).parent / "content"

if app.config["APP_ENV"] == "production":
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("A strong SECRET_KEY is required in production.")
    if not all(
        app.config[key]
        for key in ("ADMIN_PASSWORD", "GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_BRANCH", "GITHUB_CONTENT_ROOT")
    ):
        raise RuntimeError("Admin credentials and GitHub content persistence must be configured in production.")

csrf = CSRFProtect(app)


def build_content_repository():
    store = GitHubContentStore(
        token=app.config["GITHUB_TOKEN"],
        owner=app.config["GITHUB_OWNER"],
        repo=app.config["GITHUB_REPO"],
        branch=app.config["GITHUB_BRANCH"],
        content_root=app.config["GITHUB_CONTENT_ROOT"],
    )
    return ContentRepository(app.config["CONTENT_LOCAL_ROOT"], store)


content_repository = build_content_repository()
ADMIN_COLLECTIONS = {
    "news": "News and announcements",
    "opportunities": "Opportunities",
    "founders": "Founders",
    "national_heads": "National heads",
}
ADMIN_DOCUMENTS = {**ADMIN_COLLECTIONS, "site": "Homepage / site settings", "about": "About page"}
login_attempts = {}


def admin_authenticated():
    return session.get("_adh_admin_authenticated") is True


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not admin_authenticated():
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_persistence_ready():
    return all(
        app.config[key]
        for key in (
            "ADMIN_PASSWORD",
            "GITHUB_TOKEN",
            "GITHUB_OWNER",
            "GITHUB_REPO",
            "GITHUB_BRANCH",
            "GITHUB_CONTENT_ROOT",
        )
    )


def remote_admin_login_limited():
    now = time.time()
    remote = request.remote_addr or "unknown"
    attempts = [stamp for stamp in login_attempts.get(remote, []) if now - stamp < 300]
    login_attempts[remote] = attempts
    if len(attempts) >= 10:
        return True
    attempts.append(now)
    return False


@app.context_processor
def inject_site_content():
    try:
        return {"site_content": content_repository.load("site")}
    except ContentError:
        return {"site_content": {"organization_name": "ADHAYAN", "tagline": "Learn deeply. Build what matters."}}


@app.route("/")
def home():
    try:
        site = content_repository.load("site")
        news = content_repository.featured_news()
        opportunities = [item for item in content_repository.public_opportunities() if item["featured"]][:3]
    except ContentError:
        abort(503)
    featured_ids = site.get("featured_news", [])
    if site.get("featured_news_override", False):
        news = [item for item in content_repository.public_news() if item["id"] in featured_ids]
    home_sections = sorted(
        (section for section in site["home_sections"] if section.get("active", True)),
        key=lambda section: section["order"],
    )
    return render_template(
        "public_home.html",
        site=site,
        home_sections=home_sections,
        featured_news=news[:3],
        featured_opportunities=opportunities,
    )


@app.route("/health")
def health():
    return "OK", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/about")
def about():
    try:
        about_content = content_repository.load("about")
        founders = content_repository.public_people("founders")
        national_heads = content_repository.public_people("national_heads")
    except ContentError:
        abort(503)
    heads_by_country = {}
    for person in national_heads:
        heads_by_country.setdefault(person["country"], []).append(person)
    sections = sorted(
        (section for section in about_content["sections"] if section.get("active", True)),
        key=lambda section: section["order"],
    )
    return render_template(
        "about.html",
        about_content=about_content,
        sections=sections,
        founders=founders,
        heads_by_country=heads_by_country,
    )


@app.route("/news")
def news_index():
    try:
        news = content_repository.public_news()
    except ContentError:
        abort(503)
    return render_template("news.html", news=news)


@app.route("/news/<string:slug>")
def news_detail(slug):
    try:
        article = content_repository.public_news_item(slug)
    except ContentError:
        abort(503)
    if article is None:
        abort(404)
    return render_template("news_detail.html", article=article)


@app.route("/opportunities")
def opportunities():
    try:
        items = content_repository.public_opportunities()
    except ContentError:
        abort(503)
    return render_template("public_opportunities.html", opportunities=items)


@app.route("/opportunities/<string:slug>")
def opportunity_detail(slug):
    try:
        opportunity = content_repository.public_opportunity(slug)
    except ContentError:
        abort(503)
    if opportunity is None:
        abort(404)
    return render_template("public_opportunity_detail.html", opportunity=opportunity)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if admin_authenticated():
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        username_valid = hmac.compare_digest(
            username.encode("utf-8"), app.config["ADMIN_USERNAME"].encode("utf-8")
        )
        configured_password = app.config["ADMIN_PASSWORD"]
        password_valid = bool(configured_password) and hmac.compare_digest(
            password.encode("utf-8"), configured_password.encode("utf-8")
        )
        rate_limited = remote_admin_login_limited()
        if username_valid and password_valid and not rate_limited:
            session.clear()
            session.permanent = True
            session["_adh_admin_authenticated"] = True
            session["_adh_admin_logged_in_at"] = int(time.time())
            login_attempts.pop(request.remote_addr or "unknown", None)
            return redirect(url_for("admin_dashboard"))
        flash("Unable to sign in with those credentials. Please try again.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    flash("You have been signed out of the admin area.", "success")
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    counts = {}
    for name in ADMIN_COLLECTIONS:
        try:
            records = content_repository.load(name)
            counts[name] = len(records)
        except ContentError as error:
            counts[name] = None
    return render_template(
        "admin_dashboard.html",
        documents=ADMIN_COLLECTIONS,
        counts=counts,
        persistence_ready=admin_persistence_ready(),
    )


def _error_for_admin(error):
    app.logger.error("Admin content operation failed: %s", type(error).__name__)
    if isinstance(error, ContentValidationError):
        return str(error)
    if isinstance(error, ContentError):
        return str(error)
    if isinstance(error, GitHubContentError):
        return str(error)
    return "The content change could not be saved. No changes were applied."


def _load_admin_collection(collection):
    if collection not in COLLECTIONS:
        abort(404)
    try:
        return content_repository.list_records(collection)
    except ContentError:
        flash("This content collection is temporarily unavailable.", "error")
        return []


def _list_endpoint(collection):
    return {
        "news": "admin_news",
        "opportunities": "admin_opportunities",
        "founders": "admin_founders",
        "national_heads": "admin_national_heads",
    }[collection]


def _form_values(collection):
    values = {"id": request.form.get("slug", "").strip().lower(), "order": request.form.get("order", "10").strip()}
    if collection == "news":
        values.update({
            "title": request.form.get("title", "").strip(),
            "summary": request.form.get("summary", "").strip(),
            "body": request.form.get("body", "").strip(),
            "category": request.form.get("category", "").strip(),
            "published_at": request.form.get("published_at", "").strip(),
            "byline": request.form.get("byline", "").strip(),
            "image_url": request.form.get("image_url", "").strip(),
            "image_alt": request.form.get("image_alt", "").strip(),
            "published": request.form.get("published") == "on",
        })
    elif collection == "opportunities":
        values.update({
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "category": request.form.get("category", "").strip(),
            "topics": [part.strip() for part in re.split(r"[,\n]", request.form.get("topics", "")) if part.strip()],
            "difficulty": request.form.get("difficulty", "").strip(),
            "weekly_hours": request.form.get("weekly_hours", "0").strip(),
            "skills": [part.strip() for part in re.split(r"[,\n]", request.form.get("skills", "")) if part.strip()],
            "image_url": request.form.get("image_url", "").strip(),
            "image_alt": request.form.get("image_alt", "").strip(),
            "published": request.form.get("published") == "on",
            "closed": request.form.get("closed") == "on",
            "featured": request.form.get("featured") == "on",
        })
    else:
        values.update({
            "name": request.form.get("name", "").strip(),
            "role": request.form.get("role", "").strip(),
            "bio": request.form.get("bio", "").strip(),
            "portrait_url": request.form.get("portrait_url", "").strip(),
            "active": request.form.get("active") == "on",
        })
        if collection == "national_heads":
            values["country"] = request.form.get("country", "").strip()

    try:
        values["order"] = int(values["order"])
        if collection == "opportunities":
            values["weekly_hours"] = int(values["weekly_hours"])
    except ValueError:
        raise ContentValidationError("Order and weekly hours must be whole numbers.") from None
    return values


def _submitted_form_values(collection):
    values = request.form.to_dict()
    values["id"] = request.form.get("slug", "")
    values["order"] = request.form.get("order", "")
    if collection == "opportunities":
        values["topics"] = [part.strip() for part in re.split(r"[,\n]", request.form.get("topics", "")) if part.strip()]
        values["skills"] = [part.strip() for part in re.split(r"[,\n]", request.form.get("skills", "")) if part.strip()]
    for checkbox in ("published", "featured", "closed", "active"):
        if checkbox in values:
            values[checkbox] = True
        else:
            values[checkbox] = False
    return values


def _render_record_form(collection, record_id=None):
    if collection not in COLLECTIONS:
        abort(404)
    records = content_repository.list_records(collection)
    original = None
    if record_id is not None:
        original = next((record for record in records if record["id"] == record_id), None)
        if original is None:
            abort(404)

    values = dict(original or {"order": (len(records) + 1) * 10})
    errors = []
    if request.method == "POST":
        try:
            values = _form_values(collection)
            content_repository.save_record(
                collection,
                values,
                f"{'Update' if original else 'Add'} ADHAYAN {collection} record",
                current_id=record_id,
            )
        except (ContentError, GitHubContentError) as error:
            values = _submitted_form_values(collection)
            errors.append(_error_for_admin(error))
        else:
            flash(f"{ADMIN_COLLECTIONS[collection]} saved successfully.", "success")
            return redirect(url_for(_list_endpoint(collection)))

    return render_template(
        "admin_record_form.html",
        collection=collection,
        label=ADMIN_COLLECTIONS[collection],
        values=values,
        errors=errors,
        editing=original is not None,
        persistence_ready=admin_persistence_ready(),
    ), 400 if errors else 200


def _delete_record(collection, record_id):
    if collection not in COLLECTIONS:
        abort(404)
    records = content_repository.list_records(collection)
    record = next((item for item in records if item["id"] == record_id), None)
    if record is None:
        abort(404)
    if request.method == "POST":
        try:
            content_repository.delete_record(collection, record_id, f"Delete ADHAYAN {collection} record {record_id}")
        except (ContentError, GitHubContentError) as error:
            flash(f"Delete failed: {_error_for_admin(error)}", "error")
            return redirect(url_for(_list_endpoint(collection)))
        flash(f"{ADMIN_COLLECTIONS[collection]} item deleted.", "success")
        return redirect(url_for(_list_endpoint(collection)))
    return render_template(
        "admin_delete_confirm.html",
        label=ADMIN_COLLECTIONS[collection],
        record=record,
        collection=collection,
        persistence_ready=admin_persistence_ready(),
    )


@app.route("/admin/site", methods=["GET", "POST"])
@admin_required
def admin_site():
    values = content_repository.load("site")
    errors = []
    published_news = [item for item in content_repository.load("news") if item["published"]]
    if not values.get("featured_news_override", False):
        values["featured_news"] = [item["id"] for item in published_news if item.get("featured", False)]
    if request.method == "POST":
        values = dict(values)
        for field in (
            "organization_name", "tagline", "hero_title", "hero_eyebrow", "intro",
            "hero_about_label", "hero_opportunities_label", "nav_cta_label",
            "about_page_eyebrow", "news_page_eyebrow", "opportunities_page_eyebrow",
            "news_section_eyebrow",
            "news_section_title", "news_all_label", "news_page_title", "news_page_intro",
            "news_empty_message", "news_read_label", "news_home_read_label", "home_news_empty_message",
            "news_detail_back_label", "featured_opportunities_eyebrow",
            "featured_opportunities_title", "featured_opportunities_all_label",
            "pathways_eyebrow", "pathways_heading", "pathways_link_label",
            "opportunities_page_title", "opportunities_page_intro", "opportunities_empty_message",
            "opportunity_more_info_label", "opportunity_open_label", "opportunity_closed_label",
            "opportunity_detail_back_label", "opportunity_difficulty_label",
            "opportunity_expected_time_label", "opportunity_skills_label", "weekly_hours_suffix",
            "about_people_count_label", "about_countries_count_label",
        ):
            values[field] = request.form.get(field, "").strip()
        values["journey_steps"] = [line.strip() for line in request.form.get("journey_steps", "").splitlines() if line.strip()]
        values["featured_news"] = request.form.getlist("featured_news")
        values["featured_news_override"] = True
        nav = []
        for page, default_label in (("about", "About"), ("opportunities", "Initiatives"), ("news", "News")):
            label = request.form.get(f"nav_{page}", default_label).strip()
            if label:
                nav.append({"page": page, "label": label})
        values["navigation"] = nav
        try:
            valid_news_ids = {item["id"] for item in published_news}
            if any(item not in valid_news_ids for item in values["featured_news"]):
                raise ContentValidationError("Featured news must refer to currently published articles.")
            content_repository.save("site", values, "Update ADHAYAN homepage and site settings")
        except (ContentError, GitHubContentError) as error:
            errors.append(_error_for_admin(error))
        else:
            flash("Homepage and site settings saved.", "success")
            return redirect(url_for("admin_site"))
    return render_template(
        "admin_site_form.html",
        values=values,
        published_news=published_news,
        errors=errors,
        persistence_ready=admin_persistence_ready(),
    ), 400 if errors else 200


@app.route("/admin/about", methods=["GET", "POST"])
@admin_required
def admin_about():
    values = content_repository.load("about")
    errors = []
    if request.method == "POST":
        values = dict(values)
        for field in (
            "title", "intro", "founders_heading", "founders_eyebrow", "founders_empty_message",
            "national_heads_heading", "national_heads_eyebrow", "national_heads_empty_message",
        ):
            values[field] = request.form.get(field, "").strip()
        try:
            content_repository.save("about", values, "Update ADHAYAN About page")
        except (ContentError, GitHubContentError) as error:
            errors.append(_error_for_admin(error))
        else:
            flash("About page copy saved.", "success")
            return redirect(url_for("admin_about"))
    sections = sorted(values.get("sections", []), key=lambda section: section.get("order", 0))
    return render_template("admin_about_form.html", values=values, sections=sections, errors=errors,
                           persistence_ready=admin_persistence_ready()), 400 if errors else 200


def _section_form(document, record_id=None):
    if document not in {"site", "about"}:
        abort(404)
    key = "home_sections" if document == "site" else "sections"
    values = content_repository.load(document)
    sections = values[key]
    existing = next((item for item in sections if item["id"] == record_id), None) if record_id else None
    if record_id and existing is None:
        abort(404)
    section = dict(existing or {"order": (len(sections) + 1) * 10, "active": True, "eyebrow": "", "link_page": "", "link_label": ""})
    errors = []
    if request.method == "POST":
        section = {
            "id": request.form.get("slug", "").strip().lower(),
            "eyebrow": request.form.get("eyebrow", "").strip(),
            "heading": request.form.get("heading", "").strip(),
            "body": request.form.get("body", "").strip(),
            "order": request.form.get("order", "10").strip(),
            "active": request.form.get("active") == "on",
            "link_page": request.form.get("link_page", "").strip(),
            "link_label": request.form.get("link_label", "").strip(),
        }
        try:
            section["order"] = int(section["order"])
            updated_sections = [item for item in sections if item["id"] != record_id]
            if any(item["id"] == section["id"] for item in updated_sections):
                raise ContentValidationError("A section with this slug already exists.")
            updated_sections.append(section)
            values[key] = updated_sections
            content_repository.save(document, values, f"Update ADHAYAN {document} section")
        except (ValueError, ContentError, GitHubContentError) as error:
            errors.append("Order must be a whole number." if isinstance(error, ValueError) else _error_for_admin(error))
        else:
            flash("Section saved successfully.", "success")
            return redirect(url_for("admin_about" if document == "about" else "admin_site"))
    return render_template("admin_section_form.html", document=document, section=section, editing=existing is not None,
                           errors=errors, persistence_ready=admin_persistence_ready()), 400 if errors else 200


def _delete_section(document, record_id):
    if document not in {"site", "about"}:
        abort(404)
    key = "home_sections" if document == "site" else "sections"
    values = content_repository.load(document)
    section = next((item for item in values[key] if item["id"] == record_id), None)
    if section is None:
        abort(404)
    if request.method == "POST":
        values[key] = [item for item in values[key] if item["id"] != record_id]
        try:
            content_repository.save(document, values, f"Delete ADHAYAN {document} section")
        except (ContentError, GitHubContentError) as error:
            flash(f"Delete failed: {_error_for_admin(error)}", "error")
        else:
            flash("Section deleted.", "success")
        return redirect(url_for("admin_about" if document == "about" else "admin_site"))
    return render_template("admin_delete_confirm.html", label="section", record=section, collection=document,
                           persistence_ready=admin_persistence_ready(), section=True)


@app.route("/admin/news")
@admin_required
def admin_news():
    try:
        site = content_repository.load("site")
        featured_ids = site.get("featured_news", [])
        if not site.get("featured_news_override", False):
            featured_ids = [item["id"] for item in content_repository.load("news") if item.get("featured")]
    except ContentError:
        featured_ids = []
    return render_template("admin_collection.html", name="news", label=ADMIN_COLLECTIONS["news"],
                           records=_load_admin_collection("news"), featured_ids=featured_ids,
                           persistence_ready=admin_persistence_ready())


@app.route("/admin/news/featured", methods=["POST"])
@admin_required
def admin_news_featured():
    try:
        site = content_repository.load("site")
        published_ids = {item["id"] for item in content_repository.load("news") if item["published"]}
        selected_ids = request.form.getlist("featured_news")
        if any(identifier not in published_ids for identifier in selected_ids):
            raise ContentValidationError("Only published news articles can be featured.")
        site["featured_news"] = selected_ids
        site["featured_news_override"] = True
        content_repository.save("site", site, "Update ADHAYAN featured news selection")
    except (ContentError, GitHubContentError) as error:
        flash(f"Featured news was not saved: {_error_for_admin(error)}", "error")
    else:
        flash("Featured news selection saved.", "success")
    return redirect(url_for("admin_news"))


@app.route("/admin/opportunities")
@admin_required
def admin_opportunities():
    return render_template("admin_collection.html", name="opportunities", label=ADMIN_COLLECTIONS["opportunities"],
                           records=_load_admin_collection("opportunities"), persistence_ready=admin_persistence_ready())


@app.route("/admin/founders")
@admin_required
def admin_founders():
    return render_template("admin_collection.html", name="founders", label=ADMIN_COLLECTIONS["founders"],
                           records=_load_admin_collection("founders"), persistence_ready=admin_persistence_ready())


@app.route("/admin/national-heads")
@admin_required
def admin_national_heads():
    return render_template("admin_collection.html", name="national_heads", label=ADMIN_COLLECTIONS["national_heads"],
                           records=_load_admin_collection("national_heads"), persistence_ready=admin_persistence_ready())


@app.route("/admin/<string:collection>/new", methods=["GET", "POST"])
@admin_required
def admin_record_new(collection):
    return _render_record_form(collection)


@app.route("/admin/<string:collection>/<string:record_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_record_edit(collection, record_id):
    return _render_record_form(collection, record_id)


@app.route("/admin/<string:collection>/<string:record_id>/delete", methods=["GET", "POST"])
@admin_required
def admin_record_delete(collection, record_id):
    return _delete_record(collection, record_id)


@app.route("/admin/<string:document>/sections/new", methods=["GET", "POST"])
@admin_required
def admin_section_new(document):
    return _section_form(document)


@app.route("/admin/<string:document>/sections/<string:record_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_section_edit(document, record_id):
    return _section_form(document, record_id)


@app.route("/admin/<string:document>/sections/<string:record_id>/delete", methods=["GET", "POST"])
@admin_required
def admin_section_delete(document, record_id):
    return _delete_section(document, record_id)


@app.route("/admin/settings")
@admin_required
def admin_settings():
    return render_template(
        "admin_settings.html",
        persistence_ready=admin_persistence_ready(),
        github_owner=app.config["GITHUB_OWNER"],
        github_repo=app.config["GITHUB_REPO"],
        github_branch=app.config["GITHUB_BRANCH"],
        content_root=app.config["GITHUB_CONTENT_ROOT"],
    )


@app.before_request
def enforce_admin_session_lifetime():
    logged_in_at = session.get("_adh_admin_logged_in_at")
    lifetime = app.permanent_session_lifetime.total_seconds()
    if logged_in_at is not None and time.time() - logged_in_at > lifetime:
        session.clear()


@app.after_request
def add_security_headers(response):
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' data: https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data:; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'; object-src 'none'; upgrade-insecure-requests",
    )
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    return render_template("error.html", code=400, message="The form security token is invalid or missing."), 400


@app.errorhandler(ContentError)
@app.errorhandler(GitHubContentError)
def handle_content_unavailable(error):
    app.logger.error("Content operation failed: %s", type(error).__name__)
    return render_template("error.html", code=503, message="ADHAYAN content is temporarily unavailable."), 503


@app.errorhandler(404)
def handle_not_found(error):
    return render_template("error.html", code=404, message="The page you requested does not exist."), 404


@app.errorhandler(400)
def handle_bad_request(error):
    return render_template("error.html", code=400, message="The request could not be processed."), 400


@app.errorhandler(503)
def handle_service_unavailable(error):
    return render_template("error.html", code=503, message="ADHAYAN content is temporarily unavailable."), 503


@app.errorhandler(500)
def handle_server_error(error):
    app.logger.exception("Unhandled application error")
    return render_template("error.html", code=500, message="ADHAYAN could not complete that request."), 500


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="0.0.0.0", port=5000)