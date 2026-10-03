import base64
import json
import re
import shutil
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError, URLError

import pytest

import app as app_module
from app import app
from content_service import DEFAULT_ABOUT, DEFAULT_SITE, ContentRepository, ContentValidationError, normalize_document, validate_document
from github_persistence import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubConflictError,
    GitHubContentStore,
    GitHubNetworkError,
)


ROOT = Path(__file__).resolve().parents[1]


class MemoryGitHubStore:
    configured = True

    def __init__(self, content_root):
        self.files = {
            path: (ROOT / "content" / path).read_text(encoding="utf-8")
            for path in (
                "site.json",
                "about.json",
                "news.json",
                "opportunities.json",
                "people/founders.json",
                "people/national_heads.json",
            )
        }
        self.writes = []
        self.fail_writes = False

    def read_file(self, path):
        value = self.files.get(path)
        if value is None:
            return None
        return value, f"sha-{len(value)}"

    def write_file(self, path, content, message):
        if self.fail_writes:
            raise GitHubAPIError("mock GitHub failure")
        self.files[path] = content
        self.writes.append((path, content, message))
        return "new-sha"


@pytest.fixture
def client(tmp_path, monkeypatch):
    content_root = tmp_path / "content"
    shutil.copytree(ROOT / "content", content_root)
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="test-session-signing-key",
        SESSION_COOKIE_SECURE=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        ADMIN_USERNAME="admin",
        ADMIN_PASSWORD="test-admin-password",
        ADMIN_SESSION_LIFETIME_SECONDS=1800,
        PERMANENT_SESSION_LIFETIME=1800,
        GITHUB_TOKEN="mock-token",
        GITHUB_OWNER="test-owner",
        GITHUB_REPO="test-repo",
        GITHUB_BRANCH="main",
        GITHUB_CONTENT_ROOT="content",
    )
    memory_store = MemoryGitHubStore(content_root)
    monkeypatch.setattr(app_module, "content_repository", ContentRepository(content_root, memory_store))
    app_module.login_attempts.clear()
    with app.test_client() as test_client:
        yield test_client, memory_store, content_root


def valid_news(**overrides):
    item = {
        "id": "field-notes",
        "title": "Field notes",
        "summary": "A short published update.",
        "body": "First paragraph.\nSecond paragraph.",
        "category": "Update",
        "published_at": "2026-09-28",
        "byline": "ADHAYAN",
        "image_url": "",
        "published": True,
        "featured": True,
    }
    item.update(overrides)
    return item


def valid_opportunity(**overrides):
    item = {
        "id": "open-research",
        "title": "Open research initiative",
        "description": "A public initiative description.",
        "category": "Research",
        "topics": ["Learning", "Research"],
        "difficulty": "Intermediate",
        "weekly_hours": 6,
        "skills": ["Writing"],
        "published": True,
        "closed": False,
        "image_url": "",
        "featured": True,
        "order": 1,
    }
    item.update(overrides)
    return item


def opportunity_form(**overrides):
    form = {
        "slug": "open-research",
        "title": "Open research initiative",
        "description": "A public initiative description.",
        "category": "Research",
        "topics": "Learning, Research",
        "difficulty": "Intermediate",
        "weekly_hours": "6",
        "skills": "Writing",
        "image_url": "",
        "image_alt": "",
        "published": "on",
        "featured": "on",
        "order": "10",
    }
    form.update(overrides)
    return form


def person_form(kind, **overrides):
    form = {
        "slug": f"{kind}-person",
        "name": f"{kind.title()} Person",
        "role": "Coordinator",
        "bio": "A short approved biography.",
        "portrait_url": "",
        "order": "10",
        "active": "on",
    }
    if kind == "national":
        form["country"] = "Canada"
    form.update(overrides)
    return form


def login_admin(client):
    return client.post(
        "/admin/login",
        data={"username": "admin", "password": "test-admin-password"},
        follow_redirects=True,
    )


def test_anonymous_visitors_can_access_public_pages(client):
    test_client, _, _ = client
    for path in ("/", "/about", "/news", "/opportunities"):
        response = test_client.get(path)
        assert response.status_code == 200
        assert b"ADHAYAN" in response.data
    for retired_path in ("/auth", "/auth/login", "/dashboard", "/logout"):
        assert test_client.get(retired_path).status_code == 404


def test_public_news_exposes_published_items_and_hides_drafts(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([
        valid_news(),
        valid_news(id="draft-note", title="Private draft", published=False, featured=True),
    ])
    index = test_client.get("/news")
    assert index.status_code == 200
    assert b"Field notes" in index.data
    assert b"Private draft" not in index.data
    assert test_client.get("/news/field-notes").status_code == 200
    assert b"First paragraph." in test_client.get("/news/field-notes").data
    assert b"Private draft" not in test_client.get("/news/draft-note").data
    assert test_client.get("/news/draft-note").status_code == 404


def test_public_admin_content_is_autoescaped(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([valid_news(body="<script>alert(1)</script>")])
    store.files["opportunities.json"] = json.dumps([valid_opportunity(description="<img src=x onerror=alert(1)>")])
    news_response = test_client.get("/news/field-notes")
    opportunity_response = test_client.get("/opportunities/open-research")
    assert b"<script>alert(1)</script>" not in news_response.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in news_response.data
    assert b"<img src=x onerror=alert(1)>" not in opportunity_response.data
    assert b"&lt;img src=x onerror=alert(1)&gt;" in opportunity_response.data


def test_homepage_selects_featured_news_from_site_configuration(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([
        valid_news(),
        valid_news(id="not-featured", title="Not selected", featured=False),
    ])
    site = json.loads(store.files["site.json"])
    site["featured_news"] = ["field-notes"]
    store.files["site.json"] = json.dumps(site)
    response = test_client.get("/")
    assert response.status_code == 200
    assert b"Field notes" in response.data
    assert b"Not selected" not in response.data


def test_public_opportunities_and_people_are_sorted_and_filtered(client):
    test_client, store, _ = client
    store.files["opportunities.json"] = json.dumps([
        valid_opportunity(id="closed-item", title="Closed initiative", closed=True, order=2),
        valid_opportunity(id="draft-item", title="Draft initiative", published=False, order=0),
        valid_opportunity(id="active-item", title="Active initiative", order=1),
    ])
    store.files["people/founders.json"] = json.dumps([
        {"id": "founder-b", "name": "B Founder", "role": "Co-founder", "bio": "Bio B", "portrait_url": "", "order": 2, "active": True},
        {"id": "founder-a", "name": "A Founder", "role": "Founder", "bio": "Bio A", "portrait_url": "", "order": 1, "active": True},
        {"id": "inactive-founder", "name": "Inactive", "role": "Former", "bio": "Hidden", "portrait_url": "", "order": 0, "active": False},
    ])
    store.files["people/national_heads.json"] = json.dumps([
        {"id": "head-us", "name": "US Head", "role": "National Head", "bio": "Bio", "country": "United States", "portrait_url": "", "order": 2, "active": True},
        {"id": "head-ca", "name": "Canada Head", "role": "National Head", "bio": "Bio", "country": "Canada", "portrait_url": "", "order": 1, "active": True},
    ])
    opportunities = test_client.get("/opportunities")
    assert opportunities.status_code == 200
    assert b"Active initiative" in opportunities.data
    assert b"Closed initiative" in opportunities.data
    assert b"Draft initiative" not in opportunities.data
    assert test_client.get("/opportunities/active-item").status_code == 200
    assert test_client.get("/opportunities/draft-item").status_code == 404
    about = test_client.get("/about")
    assert about.status_code == 200
    assert about.data.index(b"A Founder") < about.data.index(b"B Founder")
    assert b"Inactive" not in about.data
    assert about.data.index(b"Canada") < about.data.index(b"United States")


def test_malformed_content_fails_safely_without_exposing_parser_details(client):
    test_client, store, _ = client
    store.files["news.json"] = "{ not valid JSON"
    response = test_client.get("/news")
    assert response.status_code == 503
    assert b"temporarily unavailable" in response.data
    assert b"JSONDecodeError" not in response.data


def test_admin_authentication_and_protected_routes(client):
    test_client, _, _ = client
    blocked = test_client.get("/admin", follow_redirects=False)
    assert blocked.status_code == 302
    assert "/admin/login" in blocked.headers["Location"]
    assert test_client.get("/admin/login").status_code == 200
    denied = test_client.post("/admin/login", data={"username": "wrong", "password": "test-admin-password"})
    assert denied.status_code == 200
    assert b"Unable to sign in" in denied.data
    assert b"username" not in denied.data.lower() or b"incorrect username" not in denied.data.lower()
    unicode_denied = test_client.post("/admin/login", data={"username": "管理者", "password": "пароль"})
    assert unicode_denied.status_code == 200
    assert b"Unable to sign in" in unicode_denied.data
    assert login_admin(test_client).status_code == 200
    assert test_client.get("/admin").status_code == 200
    assert b"News &amp; announcements" in test_client.get("/admin").data


def test_admin_login_attempts_are_rate_limited(client):
    test_client, _, _ = client
    responses = [
        test_client.post("/admin/login", data={"username": "admin", "password": "wrong-password"})
        for _ in range(10)
    ]
    assert all(response.status_code == 200 for response in responses)
    locked_out = test_client.post(
        "/admin/login",
        data={"username": "admin", "password": "test-admin-password"},
    )
    assert locked_out.status_code == 200
    assert b"Unable to sign in" in locked_out.data
    assert test_client.get("/admin", follow_redirects=False).status_code == 302


def test_admin_logout_and_session_expiry(client):
    test_client, _, _ = client
    login_admin(test_client)
    with test_client.session_transaction() as current_session:
        current_session["_adh_admin_logged_in_at"] = 1
        current_session.permanent = True
    expired = test_client.get("/admin", follow_redirects=False)
    assert expired.status_code == 302
    login_admin(test_client)
    logged_out = test_client.post("/admin/logout", follow_redirects=False)
    assert logged_out.status_code == 302
    assert test_client.get("/admin", follow_redirects=False).status_code == 302


def test_admin_record_form_create_edit_delete_persists_through_content_repository(client):
    test_client, store, _ = client
    login_admin(test_client)
    form = {
        "slug": "field-notes",
        "title": "Field notes",
        "summary": "A short published update.",
        "body": "First paragraph.\nSecond paragraph.",
        "category": "Update",
        "published_at": "2026-09-28",
        "byline": "ADHAYAN",
        "image_url": "",
        "image_alt": "",
        "order": "10",
        "published": "on",
        "featured": "on",
    }
    response = test_client.post("/admin/news/new", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert b"News and announcements saved successfully" in response.data
    assert b"Field notes" in response.data
    assert store.writes[-1][0] == "news.json"
    assert "Add ADHAYAN news record" == store.writes[-1][2]

    edit = dict(form, title="Revised field notes")
    response = test_client.post("/admin/news/field-notes/edit", data=edit, follow_redirects=True)
    assert response.status_code == 200
    assert b"Revised field notes" in response.data
    assert len(json.loads(store.files["news.json"])) == 1
    unpublished = dict(edit)
    unpublished.pop("published")
    test_client.post("/admin/news/field-notes/edit", data=unpublished, follow_redirects=True)
    assert test_client.get("/news/field-notes").status_code == 404

    confirmation = test_client.get("/admin/news/field-notes/delete")
    assert confirmation.status_code == 200
    deleted = test_client.post("/admin/news/field-notes/delete", data={"csrf_token": ""}, follow_redirects=True)
    assert deleted.status_code == 200
    assert json.loads(store.files["news.json"]) == []


def test_admin_record_save_reports_github_failure_without_success(client):
    test_client, store, _ = client
    login_admin(test_client)

    store.fail_writes = True
    failed = test_client.post(
        "/admin/news/new",
        data={
            "slug": "field-notes", "title": "Field notes", "summary": "Summary", "body": "Body",
            "category": "Update", "published_at": "2026-09-28", "byline": "ADHAYAN", "order": "10",
        },
        follow_redirects=True,
    )
    assert failed.status_code == 400
    assert b"could not be reached" not in failed.data
    assert b"Field notes" in failed.data
    assert not json.loads(store.files["news.json"])


def test_admin_record_validation_preserves_form_values(client):
    test_client, store, _ = client
    login_admin(test_client)
    invalid = test_client.post("/admin/news/new", data={
        "slug": "Bad Slug", "title": "Retained title", "summary": "Summary", "body": "Body",
        "category": "Update", "published_at": "not-a-date", "byline": "ADHAYAN", "order": "bad",
    })
    assert invalid.status_code == 400
    assert b"Retained title" in invalid.data
    assert b"Slug" in invalid.data or b"slug" in invalid.data
    assert not store.writes


def test_admin_duplicate_and_invalid_slugs_are_rejected(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([valid_news()])
    login_admin(test_client)
    duplicate = test_client.post("/admin/news/new", data={
        "slug": "field-notes", "title": "Another article", "summary": "Summary", "body": "Body",
        "category": "Update", "published_at": "2026-09-28", "byline": "ADHAYAN", "order": "20",
    })
    assert duplicate.status_code == 400
    assert b"slug already exists" in duplicate.data.lower()
    invalid_url = test_client.post("/admin/news/new", data={
        "slug": "unsafe-url", "title": "Unsafe URL", "summary": "Summary", "body": "Body",
        "category": "Update", "published_at": "2026-09-28", "byline": "ADHAYAN", "order": "20",
        "image_url": "http://example.com/image.png",
    })
    assert invalid_url.status_code == 400
    assert b"HTTPS" in invalid_url.data
    assert not store.writes


def test_admin_never_claims_success_when_github_is_unconfigured(client, monkeypatch):
    test_client, store, content_root = client
    unconfigured_store = GitHubContentStore(None, None, None, None)
    monkeypatch.setattr(app_module, "content_repository", ContentRepository(content_root, unconfigured_store))
    app.config["GITHUB_TOKEN"] = ""
    login_admin(test_client)
    response = test_client.post("/admin/news/new", data={
        "slug": "field-notes", "title": "Field notes", "summary": "Summary", "body": "Body",
        "category": "Update", "published_at": "2026-09-28", "byline": "ADHAYAN", "order": "10",
    })
    assert response.status_code == 400
    assert b"GitHub content persistence is not fully configured" in response.data
    assert not store.writes


def test_admin_site_form_updates_home_copy_navigation_featured_news_and_public_page(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([valid_news()])
    store.files["opportunities.json"] = json.dumps([valid_opportunity()])
    store.files["people/founders.json"] = json.dumps([{
        "id": "founder-one", "name": "Founder One", "role": "Founder", "bio": "Approved bio",
        "portrait_url": "", "order": 1, "active": True,
    }])
    store.files["people/national_heads.json"] = json.dumps([{
        "id": "head-one", "name": "Country Head", "role": "Head", "country": "Canada",
        "bio": "Approved bio", "portrait_url": "", "order": 1, "active": True,
    }])
    login_admin(test_client)
    assert b"content_json" not in test_client.get("/admin/site").data
    data = dict(DEFAULT_SITE)
    data.update({
        "organization_name": "ADHAYAN Collective",
        "tagline": "Learn, then build.",
        "hero_title": "A new public headline",
        "hero_eyebrow": "A custom eyebrow",
        "intro": "A carefully reviewed public introduction.",
        "hero_about_label": "Our story",
        "hero_opportunities_label": "Current initiatives",
        "nav_cta_label": "Updates from us",
        "about_page_eyebrow": "Organization story",
        "news_page_eyebrow": "Dispatches",
        "opportunities_page_eyebrow": "Ways to contribute",
        "news_section_eyebrow": "Latest",
        "news_section_title": "Updates from the team",
        "news_all_label": "Read the archive",
        "news_page_title": "Newsroom",
        "news_page_intro": "The approved news introduction.",
        "news_empty_message": "No updates are available.",
        "news_read_label": "Open article",
        "news_home_read_label": "Read feature",
        "home_news_empty_message": "Home news is empty.",
        "news_detail_back_label": "Return to dispatches",
        "featured_opportunities_eyebrow": "Join in",
        "featured_opportunities_title": "Open work",
        "featured_opportunities_all_label": "Browse work",
        "pathways_eyebrow": "Pathways",
        "pathways_heading": "A chosen homepage band.",
        "pathways_link_label": "See initiatives",
        "opportunities_page_title": "Public initiatives",
        "opportunities_page_intro": "The approved initiatives introduction.",
        "opportunities_empty_message": "No opportunities available.",
        "opportunity_more_info_label": "View initiative",
        "opportunity_open_label": "Accepting participants",
        "opportunity_closed_label": "Not accepting participants",
        "opportunity_detail_back_label": "Return to opportunities",
        "opportunity_difficulty_label": "Experience level",
        "opportunity_expected_time_label": "Time each week",
        "opportunity_skills_label": "Useful skills",
        "weekly_hours_suffix": "hours weekly",
        "about_people_count_label": "colleagues",
        "about_countries_count_label": "represented places",
        "journey_steps": ["Discover", "Contribute"],
        "featured_news": ["field-notes"],
        "featured_news_override": True,
        "home_sections": [],
        "navigation": [
            {"page": "about", "label": "Our story"},
            {"page": "opportunities", "label": "Current initiatives"},
            {"page": "news", "label": "Newsroom"},
        ],
    })
    form = {key: value for key, value in data.items() if key not in {"journey_steps", "featured_news", "featured_news_override", "home_sections", "navigation"}}
    form.update({
        "journey_steps": "Discover\nContribute",
        "featured_news": ["field-notes"],
        "nav_about": "Our story",
        "nav_opportunities": "Current initiatives",
        "nav_news": "Newsroom",
    })
    response = test_client.post("/admin/site", data=form, follow_redirects=True)
    assert response.status_code == 200
    saved_site = json.loads(store.files["site.json"])
    assert saved_site["hero_title"] == "A new public headline"
    assert saved_site["featured_news"] == ["field-notes"]
    assert saved_site["navigation"][0]["label"] == "Our story"
    public_home = test_client.get("/")
    assert b"A new public headline" in public_home.data
    assert b"Field notes" in public_home.data
    assert b"Our story" in public_home.data
    assert b"Updates from us" in public_home.data
    assert b"Read feature" in public_home.data
    public_news = test_client.get("/news")
    assert b"Newsroom" in public_news.data
    assert b"The approved news introduction." in public_news.data
    assert b"Dispatches" in public_news.data
    assert b"Return to dispatches" in test_client.get("/news/field-notes").data
    public_opportunities = test_client.get("/opportunities")
    assert b"Ways to contribute" in public_opportunities.data
    assert b"Accepting participants" in public_opportunities.data
    assert b"View initiative" in public_opportunities.data
    opportunity_detail = test_client.get("/opportunities/open-research")
    assert b"Return to opportunities" in opportunity_detail.data
    assert b"Experience level" in opportunity_detail.data
    assert b"Time each week" in opportunity_detail.data
    assert b"Useful skills" in opportunity_detail.data
    assert b"6 hours weekly" in opportunity_detail.data
    about_form = dict(DEFAULT_ABOUT)
    about_form.update({"title": "About", "intro": "Intro"})
    test_client.post("/admin/about", data=about_form)
    public_about = test_client.get("/about").data
    assert b"Organization story" in public_about
    assert b'<span class="people-count">1 <small>colleagues</small></span>' in public_about
    assert b'<span class="people-count">1 <small>represented places</small></span>' in public_about
    invalid_feature = dict(form, featured_news=["does-not-exist"])
    invalid_response = test_client.post("/admin/site", data=invalid_feature)
    assert invalid_response.status_code == 400
    assert b"currently published articles" in invalid_response.data


def test_admin_site_can_manage_custom_home_sections(client):
    test_client, store, _ = client
    login_admin(test_client)
    form = {
        "slug": "our-approach", "eyebrow": "How we work", "heading": "A public section",
        "body": "This section is authored through the admin form.", "order": "5", "active": "on",
        "link_page": "about", "link_label": "Our story",
    }
    response = test_client.post("/admin/site/sections/new", data=form, follow_redirects=True)
    assert response.status_code == 200
    assert b"A public section" in response.data
    assert b"This section is authored through the admin form" in test_client.get("/").data
    edit = dict(form, heading="Revised section")
    test_client.post("/admin/site/sections/our-approach/edit", data=edit, follow_redirects=True)
    assert b"Revised section" in test_client.get("/").data
    assert "Update ADHAYAN site section" in store.writes[-1][2]


def test_news_featured_selection_is_form_based_persisted_and_public(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([
        valid_news(),
        valid_news(id="second-note", title="Second note", featured=False),
        valid_news(id="draft-note", title="Draft note", published=False),
    ])
    login_admin(test_client)
    listing = test_client.get("/admin/news")
    assert listing.status_code == 200
    assert b"content_json" not in listing.data
    assert b"Featured news" in listing.data
    featured = test_client.post(
        "/admin/news/featured",
        data={"featured_news": ["field-notes", "second-note"]},
        follow_redirects=True,
    )
    assert featured.status_code == 200
    assert b"Featured news selection saved" in featured.data
    stored_site = json.loads(store.files["site.json"])
    assert stored_site["featured_news"] == ["field-notes", "second-note"]
    assert b"Field notes" in test_client.get("/").data
    cleared = test_client.post("/admin/news/featured", data={}, follow_redirects=True)
    assert cleared.status_code == 200
    assert json.loads(store.files["site.json"])["featured_news"] == []
    assert b"Field notes" not in test_client.get("/").data


def test_news_featured_selection_rejects_draft_and_reports_write_failure(client):
    test_client, store, _ = client
    store.files["news.json"] = json.dumps([valid_news(published=False)])
    login_admin(test_client)
    invalid = test_client.post("/admin/news/featured", data={"featured_news": ["field-notes"]})
    assert invalid.status_code == 302
    assert json.loads(store.files["site.json"])["featured_news"] == []
    store.files["news.json"] = json.dumps([valid_news()])
    store.fail_writes = True
    failed = test_client.post("/admin/news/featured", data={"featured_news": ["field-notes"]}, follow_redirects=True)
    assert failed.status_code == 200
    assert b"Featured news was not saved" in failed.data


def test_admin_about_form_and_sections_persist_and_render_publicly(client):
    test_client, store, _ = client
    login_admin(test_client)
    about_form = dict(DEFAULT_ABOUT)
    about_form.update({
        "title": "Who we are",
        "intro": "An approved About introduction.",
        "founders_heading": "Founding team",
        "founders_eyebrow": "Origin",
        "founders_empty_message": "Founders will be announced.",
        "national_heads_heading": "Country leads",
        "national_heads_eyebrow": "Our regions",
        "national_heads_empty_message": "Regional contacts coming soon.",
    })
    saved = test_client.post("/admin/about", data=about_form, follow_redirects=True)
    assert saved.status_code == 200
    assert b"Who we are" in test_client.get("/about").data
    assert b"An approved About introduction" in test_client.get("/about").data
    section = {
        "slug": "our-purpose", "eyebrow": "Purpose", "heading": "Why ADHAYAN",
        "body": "An approved organization section.", "order": "1", "active": "on",
        "link_page": "", "link_label": "",
    }
    test_client.post("/admin/about/sections/new", data=section, follow_redirects=True)
    assert b"Why ADHAYAN" in test_client.get("/about").data
    test_client.post("/admin/about/sections/our-purpose/delete", follow_redirects=True)
    assert b"Why ADHAYAN" not in test_client.get("/about").data
    assert store.writes[-1][0] == "about.json"


def test_opportunity_admin_create_edit_publish_close_order_and_delete(client):
    test_client, store, _ = client
    login_admin(test_client)
    created = test_client.post("/admin/opportunities/new", data=opportunity_form(), follow_redirects=True)
    assert created.status_code == 200
    saved = json.loads(store.files["opportunities.json"])[0]
    assert saved["topics"] == ["Learning", "Research"]
    assert saved["weekly_hours"] == 6
    assert b"Open research initiative" in test_client.get("/opportunities").data

    duplicate = test_client.post("/admin/opportunities/new", data=opportunity_form(title="Duplicate slug"))
    assert duplicate.status_code == 400
    assert b"slug already exists" in duplicate.data.lower()
    bad_image = test_client.post("/admin/opportunities/new", data=opportunity_form(slug="bad-image", image_url="http://example.com/image.jpg"))
    assert bad_image.status_code == 400
    assert b"HTTPS" in bad_image.data
    assert len(json.loads(store.files["opportunities.json"])) == 1

    updated = opportunity_form(title="Closed and reordered", closed="on", order="1")
    test_client.post("/admin/opportunities/open-research/edit", data=updated, follow_redirects=True)
    saved = json.loads(store.files["opportunities.json"])[0]
    assert saved["published"] is True
    assert saved["closed"] is True
    assert saved["order"] == 1
    assert b"Closed and reordered" in test_client.get("/opportunities").data
    assert b"Closed" in test_client.get("/opportunities/open-research").data
    unpublished = opportunity_form(title="Closed and reordered", published="", closed="on", order="1")
    test_client.post("/admin/opportunities/open-research/edit", data=unpublished, follow_redirects=True)
    assert b"Closed and reordered" not in test_client.get("/opportunities").data
    assert test_client.get("/opportunities/open-research").status_code == 404

    test_client.get("/admin/opportunities/open-research/delete")
    test_client.post("/admin/opportunities/open-research/delete", follow_redirects=True)
    assert json.loads(store.files["opportunities.json"]) == []


def test_founder_and_national_head_crud_country_active_and_order(client):
    test_client, store, _ = client
    login_admin(test_client)
    founder = person_form("founder", slug="founder-one", name="Founder One", order="2")
    test_client.post("/admin/founders/new", data=founder, follow_redirects=True)
    assert b"Founder One" in test_client.get("/about").data
    changed = person_form("founder", slug="founder-one", name="Founder Revised", order="1", active="")
    test_client.post("/admin/founders/founder-one/edit", data=changed, follow_redirects=True)
    assert b"Founder Revised" not in test_client.get("/about").data
    saved_founder = next(record for record in json.loads(store.files["people/founders.json"]) if record["id"] == "founder-one")
    assert saved_founder["order"] == 1
    test_client.get("/admin/founders/founder-one/delete")
    test_client.post("/admin/founders/founder-one/delete", follow_redirects=True)
    assert all(record["id"] != "founder-one" for record in json.loads(store.files["people/founders.json"]))

    head = person_form("national", slug="head-canada", name="Canada Lead", country="Canada", order="4")
    test_client.post("/admin/national_heads/new", data=head, follow_redirects=True)
    second_head = person_form("national", slug="head-canada-second", name="Second Canada Lead", country="Canada", order="1")
    test_client.post("/admin/national_heads/new", data=second_head, follow_redirects=True)
    about = test_client.get("/about")
    assert b"Canada" in about.data and b"Canada Lead" in about.data
    assert about.data.index(b"Second Canada Lead") < about.data.index(b"Canada Lead")
    edit_head = person_form("national", slug="head-canada", name="Canada Lead", country="New Zealand", order="1")
    test_client.post("/admin/national_heads/head-canada/edit", data=edit_head, follow_redirects=True)
    assert b"New Zealand" in test_client.get("/about").data
    test_client.post("/admin/national_heads/head-canada/delete", follow_redirects=True)
    remaining_heads = [record["id"] for record in json.loads(store.files["people/national_heads.json"])]
    assert "head-canada" not in remaining_heads
    assert "head-canada-second" in remaining_heads


def test_nonexistent_content_edit_and_delete_return_404(client):
    test_client, _, _ = client
    login_admin(test_client)
    assert test_client.get("/admin/news/missing/edit").status_code == 404
    assert test_client.get("/admin/news/missing/delete").status_code == 404


def test_unauthenticated_admin_mutation_cannot_write(client):
    test_client, store, _ = client
    response = test_client.post("/admin/news/new", data={"slug": "not-authorized"})
    assert response.status_code == 302
    assert not store.writes


def test_admin_settings_do_not_render_github_token(client):
    test_client, _, _ = client
    login_admin(test_client)
    app.config["GITHUB_TOKEN"] = "never-render-this-token"
    for path in ("/admin/settings", "/admin/site", "/admin/news"):
        response = test_client.get(path, follow_redirects=True)
        assert response.status_code == 200
        assert b"never-render-this-token" not in response.data


def test_admin_login_requires_csrf_token(client):
    test_client, _, _ = client
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        missing = test_client.post("/admin/login", data={"username": "admin", "password": "test-admin-password"})
        assert missing.status_code == 400
        page = test_client.get("/admin/login")
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        valid = test_client.post(
            "/admin/login",
            data={"username": "admin", "password": "test-admin-password", "csrf_token": token},
        )
        assert valid.status_code == 302
        content_write_without_token = test_client.post(
            "/admin/news/new",
            data={"slug": "field-notes"},
        )
        logout_without_token = test_client.post("/admin/logout")
        assert content_write_without_token.status_code == 400
        assert logout_without_token.status_code == 400
        assert test_client.get("/admin", follow_redirects=False).status_code == 200
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_admin_login_not_configured_password_fails_generically(client):
    test_client, _, _ = client
    previous_password = app.config["ADMIN_PASSWORD"]
    app.config["ADMIN_PASSWORD"] = ""
    try:
        response = test_client.post("/admin/login", data={"username": "admin", "password": "anything"})
        assert response.status_code == 200
        assert b"Unable to sign in" in response.data
    finally:
        app.config["ADMIN_PASSWORD"] = previous_password


def test_document_validation_and_public_content_ordering(tmp_path):
    with pytest.raises(ContentValidationError):
        validate_document("news", [{"id": "same"}, {"id": "same"}])
    with pytest.raises(ContentValidationError):
        validate_document("founders", [{"id": "x", "name": "A", "role": "Founder", "bio": "Bio", "portrait_url": "javascript:alert(1)", "order": 1, "active": True}])
    repo = ContentRepository(ROOT / "content")
    with pytest.raises(ContentValidationError):
        repo.save("news", [{"id": "bad"}], "test")


def test_older_site_documents_receive_defaults_for_new_public_copy_fields():
    older_site = {
        "organization_name": "ADHAYAN",
        "tagline": "Existing tagline",
        "hero_title": "Existing title",
        "intro": "Existing introduction",
        "featured_news": [],
    }
    normalized = normalize_document("site", older_site)
    validate_document("site", normalized)
    assert normalized["nav_cta_label"] == DEFAULT_SITE["nav_cta_label"]
    assert normalized["opportunity_open_label"] == DEFAULT_SITE["opportunity_open_label"]
    assert normalized["home_news_empty_message"] == DEFAULT_SITE["home_news_empty_message"]


def test_site_managed_public_copy_is_autoescaped(client):
    test_client, _, _ = client
    login_admin(test_client)
    form = {key: value for key, value in DEFAULT_SITE.items() if key not in {"journey_steps", "featured_news", "featured_news_override", "home_sections", "navigation"}}
    form.update({
        "journey_steps": "Discover",
        "nav_about": "About",
        "nav_opportunities": "Initiatives",
        "nav_news": "News",
        "nav_cta_label": "<img src=x onerror=alert(1)>",
    })
    response = test_client.post("/admin/site", data=form, follow_redirects=True)
    assert response.status_code == 200
    page = test_client.get("/").data
    assert b"<img src=x onerror=alert(1)>" not in page
    assert b"&lt;img src=x onerror=alert(1)&gt;" in page


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class FakeContentsAPI:
    def __init__(self):
        self.files = {}
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        method = request.get_method()
        path = urlsplit(request.full_url).path.rsplit("/contents/", 1)[1]
        if method == "GET":
            if path not in self.files:
                raise HTTPError(request.full_url, 404, "missing", None, BytesIO())
            content, sha = self.files[path]
            return FakeResponse({"type": "file", "content": base64.b64encode(content.encode()).decode(), "sha": sha})
        payload = json.loads(request.data.decode())
        if method == "PUT":
            expected_sha = self.files.get(path, (None, None))[1]
            if payload.get("sha") != expected_sha:
                raise HTTPError(request.full_url, 409, "conflict", None, BytesIO())
            content = base64.b64decode(payload["content"]).decode()
            self.files[path] = (content, f"sha-{len(self.files) + 1}")
            return FakeResponse({"content": {"sha": self.files[path][1]}})
        if method == "DELETE":
            self.files.pop(path, None)
            return FakeResponse({"commit": {"sha": "deleted"}})
        raise AssertionError(f"Unexpected method {method}")


def github_store(opener):
    return GitHubContentStore("token", "owner", "repo", "main", "content", opener=opener)


def test_github_contents_api_create_update_and_delete():
    api = FakeContentsAPI()
    store = github_store(api)
    created_sha = store.write_file("news.json", "[]\n", "Create news collection")
    assert created_sha.startswith("sha-")
    create_payload = json.loads(api.requests[-1].data.decode())
    assert "sha" not in create_payload
    assert create_payload["branch"] == "main"

    store.write_file("news.json", "[1]\n", "Update news collection")
    update_payload = json.loads(api.requests[-1].data.decode())
    assert update_payload["sha"] == created_sha
    assert store.read_file("news.json")[0] == "[1]\n"

    store.delete_file("news.json", "Delete news collection")
    assert api.requests[-1].get_method() == "DELETE"
    assert store.read_file("news.json") is None


@pytest.mark.parametrize("branch", ["main", "release/cms-v1"])
def test_github_content_reads_explicitly_use_configured_branch(branch):
    api = FakeContentsAPI()
    api.files["content/news.json"] = ("[]\n", "known-sha")
    store = GitHubContentStore("token", "owner", "repo", branch, "content", opener=api)

    assert store.read_file("news.json")[0] == "[]\n"
    request = api.requests[-1]
    assert request.get_method() == "GET"
    assert parse_qs(urlsplit(request.full_url).query) == {"ref": [branch]}

    store.write_file("news.json", "[{}]\n", "Update on configured branch")
    get_request, put_request = api.requests[-2:]
    assert parse_qs(urlsplit(get_request.full_url).query) == {"ref": [branch]}
    assert put_request.get_method() == "PUT"
    assert json.loads(put_request.data.decode())["branch"] == branch


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, GitHubAuthenticationError), (409, GitHubConflictError), (422, GitHubConflictError), (500, GitHubAPIError)],
)
def test_github_api_errors_are_classified(status, error_type):
    def opener(request, timeout):
        raise HTTPError(request.full_url, status, "failure", None, BytesIO())

    with pytest.raises(error_type):
        github_store(opener).read_file("news.json")


def test_github_configuration_network_and_path_errors_are_explicit():
    with pytest.raises(GitHubConfigurationError):
        GitHubContentStore(None, None, None, None).write_file("news.json", "[]", "commit")
    with pytest.raises(GitHubConfigurationError):
        github_store(FakeContentsAPI()).read_file("../secrets.env")

    def disconnected(request, timeout):
        raise URLError("offline")

    with pytest.raises(GitHubNetworkError):
        github_store(disconnected).read_file("news.json")


def test_production_requires_secret_key_without_database_dependency(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "")
    result = __import__("subprocess").run(
        [__import__("sys").executable, "-c", "import app"],
        env=__import__("os").environ.copy(),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode != 0
    assert "A strong SECRET_KEY is required" in result.stderr


def test_production_boots_with_admin_and_github_config_without_database(monkeypatch):
    environment = __import__("os").environ.copy()
    environment.update({
        "APP_ENV": "production",
        "SECRET_KEY": "test-production-signing-key",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "test-admin-password",
        "GITHUB_TOKEN": "test-token",
        "GITHUB_OWNER": "test-owner",
        "GITHUB_REPO": "test-repo",
        "GITHUB_BRANCH": "main",
        "GITHUB_CONTENT_ROOT": "content",
        "DATABASE_URL": "",
    })
    command = "from app import app; print(app.config['DEBUG']); print('sqlalchemy' in app.extensions)"
    result = __import__("subprocess").run(
        [__import__("sys").executable, "-c", command],
        env=environment,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["False", "False"]


def test_security_headers_and_session_cookie_settings(client):
    test_client, _, _ = client
    response = test_client.get("/news")
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in response.headers["Permissions-Policy"]
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.permanent_session_lifetime.total_seconds() == 1800


def test_not_found_and_server_errors_are_generic(client, monkeypatch):
    test_client, _, _ = client
    not_found = test_client.get("/no-such-page")
    assert not_found.status_code == 404
    assert b"page you requested does not exist" in not_found.data.lower()
    method_not_allowed = test_client.get("/admin/logout")
    assert method_not_allowed.status_code == 405
    assert b"debugger" not in method_not_allowed.data.lower()

    def raise_error():
        raise RuntimeError("private internal error text")

    original_home = app.view_functions["home"]
    previous_propagation = app.config.get("PROPAGATE_EXCEPTIONS")
    app.view_functions["home"] = raise_error
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        response = test_client.get("/")
    finally:
        app.view_functions["home"] = original_home
        app.config["PROPAGATE_EXCEPTIONS"] = previous_propagation
    assert response.status_code == 500
    assert b"could not complete that request" in response.data
    assert b"private internal error text" not in response.data