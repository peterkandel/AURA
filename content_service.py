import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from github_persistence import GitHubContentStore


class ContentError(Exception):
    pass


class ContentValidationError(ContentError):
    pass


DOCUMENT_PATHS = {
    "site": "site.json",
    "about": "about.json",
    "news": "news.json",
    "opportunities": "opportunities.json",
    "founders": "people/founders.json",
    "national_heads": "people/national_heads.json",
}

COLLECTIONS = {key for key in DOCUMENT_PATHS if key not in {"site", "about"}}
NAVIGATION_PAGES = {"about", "opportunities", "news"}

DEFAULT_SITE = {
    "organization_name": "ADHAYAN",
    "tagline": "Learn deeply. Build what matters.",
    "hero_title": "Learning becomes action.",
    "hero_eyebrow": "An organization for learning in action",
    "intro": "ADHAYAN's organizational introduction will be published here.",
    "hero_about_label": "Meet ADHAYAN",
    "hero_opportunities_label": "Explore initiatives",
    "nav_cta_label": "Latest updates",
    "about_page_eyebrow": "ADHAYAN / ABOUT",
    "news_page_eyebrow": "ADHAYAN / NEWSROOM",
    "opportunities_page_eyebrow": "ADHAYAN / INITIATIVES",
    "journey_steps": ["Discover", "Join", "Work", "Contribute"],
    "news_section_eyebrow": "From the organization",
    "news_section_title": "News & announcements",
    "news_all_label": "All news",
    "news_page_title": "News & announcements",
    "news_page_intro": "Updates, notes, and announcements from ADHAYAN.",
    "news_empty_message": "There are no published updates yet.",
    "news_read_label": "Read update",
    "news_home_read_label": "Read update",
    "home_news_empty_message": "Updates from ADHAYAN will appear here.",
    "news_detail_back_label": "All news",
    "featured_opportunities_eyebrow": "Ways to take part",
    "featured_opportunities_title": "Featured initiatives",
    "featured_opportunities_all_label": "View all",
    "pathways_eyebrow": "Open pathways",
    "pathways_heading": "Ideas become shared work.",
    "pathways_link_label": "Explore initiatives",
    "opportunities_page_title": "Explore the work",
    "opportunities_page_intro": "Public initiatives and opportunities to learn, build, and contribute.",
    "opportunities_empty_message": "There are no published initiatives yet.",
    "opportunity_more_info_label": "More information",
    "opportunity_open_label": "Open",
    "opportunity_closed_label": "Closed",
    "opportunity_detail_back_label": "All initiatives",
    "opportunity_difficulty_label": "Difficulty",
    "opportunity_expected_time_label": "Expected time",
    "opportunity_skills_label": "Skills",
    "weekly_hours_suffix": "hrs/week",
    "about_people_count_label": "people",
    "about_countries_count_label": "countries",
    "home_sections": [],
    "featured_news": [],
    "featured_news_override": False,
    "navigation": [
        {"label": "About", "page": "about"},
        {"label": "Initiatives", "page": "opportunities"},
        {"label": "News", "page": "news"},
    ],
}


DEFAULT_ABOUT = {
    "title": "About ADHAYAN",
    "intro": "ADHAYAN's organizational introduction will be published here.",
    "sections": [],
    "founders_heading": "Founders",
    "founders_eyebrow": "The people who began it",
    "national_heads_heading": "National heads",
    "national_heads_eyebrow": "Across our communities",
    "founders_empty_message": "Founder information will be published here.",
    "national_heads_empty_message": "National head information will be published here.",
}


def normalize_document(name, value):
    if name == "site" and isinstance(value, dict):
        return {**DEFAULT_SITE, **value}
    if name == "about" and isinstance(value, dict):
        return {**DEFAULT_ABOUT, **value}
    if name not in COLLECTIONS or not isinstance(value, list):
        return value

    records = []
    for index, record in enumerate(value):
        if not isinstance(record, dict):
            records.append(record)
            continue
        normalized = dict(record)
        normalized.setdefault("order", (index + 1) * 10)
        if name == "news":
            normalized.setdefault("image_url", "")
            normalized.setdefault("image_alt", "")
            normalized.setdefault("published", False)
            normalized.setdefault("featured", False)
        elif name == "opportunities":
            normalized.setdefault("topics", [])
            normalized.setdefault("difficulty", "")
            normalized.setdefault("weekly_hours", 0)
            normalized.setdefault("skills", [])
            normalized.setdefault("image_url", "")
            normalized.setdefault("published", False)
            normalized.setdefault("closed", False)
            normalized.setdefault("featured", False)
        else:
            normalized.setdefault("portrait_url", "")
            normalized.setdefault("active", True)
        records.append(normalized)
    return records


def _require_text(record, field, label, max_length=10000):
    value = record.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ContentValidationError(f"{label} must be non-empty text within the allowed length.")


def _validate_person(record, national_head=False):
    _require_text(record, "id", "id", 100)
    _require_text(record, "name", "name", 160)
    _require_text(record, "role", "role", 160)
    _require_text(record, "bio", "bio", 5000)
    if national_head:
        _require_text(record, "country", "country", 120)
    _optional_url(record, "portrait_url")
    _require_order_active(record)


def _optional_url(record, field):
    value = record.get(field, "")
    if not isinstance(value, str):
        raise ContentValidationError(f"{field} must be an HTTPS URL when provided.")
    if value:
        parsed = urlsplit(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ContentValidationError(f"{field} must be a valid HTTPS URL when provided.")


def _require_order_active(record):
    _require_order(record)
    if not isinstance(record.get("active"), bool):
        raise ContentValidationError("active must be a boolean.")


def _require_order(record):
    value = record.get("order")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1000000:
        raise ContentValidationError("order must be a whole number between 0 and 1000000.")


def _validate_sections(sections, name):
    if not isinstance(sections, list):
        raise ContentValidationError(f"{name} must be a list of sections.")
    seen = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ContentValidationError(f"Each {name} section must be an object.")
        _require_text(section, "id", "section id", 100)
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", section["id"]):
            raise ContentValidationError("Section ids must be URL-safe slugs.")
        if section["id"] in seen:
            raise ContentValidationError(f"Duplicate section id '{section['id']}'.")
        seen.add(section["id"])
        _require_text(section, "heading", "section heading", 240)
        _require_text(section, "body", "section body", 10000)
        _require_order(section)
        if "eyebrow" in section and (not isinstance(section["eyebrow"], str) or len(section["eyebrow"]) > 120):
            raise ContentValidationError("Section eyebrow must be text within 120 characters.")
        if "active" in section and not isinstance(section["active"], bool):
            raise ContentValidationError("Section active state must be a boolean.")
        if section.get("link_page", "") not in {"", *NAVIGATION_PAGES}:
            raise ContentValidationError("Section link target is invalid.")
        if "link_label" in section and (not isinstance(section["link_label"], str) or len(section["link_label"]) > 120):
            raise ContentValidationError("Section link label must be text within 120 characters.")


def validate_document(name, value):
    if name in {"site", "about"}:
        if not isinstance(value, dict):
            raise ContentValidationError(f"{name.title()} content must be a JSON object.")
        config = normalize_document(name, value)
        if name == "site":
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
                _require_text(config, field, field, 1000)
            if not isinstance(config.get("journey_steps"), list) or any(not isinstance(step, str) or not step.strip() for step in config["journey_steps"]):
                raise ContentValidationError("journey_steps must be a list of non-empty strings.")
            featured = config.get("featured_news", [])
            if not isinstance(featured, list) or any(not isinstance(item, str) for item in featured):
                raise ContentValidationError("featured_news must be a list of news IDs.")
            if not isinstance(config.get("featured_news_override"), bool):
                raise ContentValidationError("featured_news_override must be a boolean.")
            navigation = config.get("navigation", [])
            if not isinstance(navigation, list):
                raise ContentValidationError("navigation must be a list.")
            seen_pages = set()
            for item in navigation:
                if not isinstance(item, dict) or item.get("page") not in NAVIGATION_PAGES:
                    raise ContentValidationError("Each navigation item must target a public page.")
                _require_text(item, "label", "navigation label", 80)
                if item["page"] in seen_pages:
                    raise ContentValidationError("Navigation pages cannot be duplicated.")
                seen_pages.add(item["page"])
            _validate_sections(config.get("home_sections", []), "home_sections")
        else:
            for field in (
                "title", "intro", "founders_heading", "founders_eyebrow", "founders_empty_message",
                "national_heads_heading", "national_heads_eyebrow", "national_heads_empty_message",
            ):
                _require_text(config, field, field, 1000)
            _validate_sections(config.get("sections", []), "sections")
        return

    value = normalize_document(name, value)
    if not isinstance(value, list):
        raise ContentValidationError(f"{name} content must be a JSON array.")
    identifiers = set()
    for record in value:
        if not isinstance(record, dict):
            raise ContentValidationError(f"Each {name} item must be a JSON object.")
        _require_text(record, "id", "id", 100)
        identifier = record["id"].strip()
        if identifier in identifiers:
            raise ContentValidationError(f"Duplicate id '{identifier}' in {name}.")
        identifiers.add(identifier)

        if name == "news":
            for field, max_length in (("title", 240), ("summary", 1000), ("body", 30000), ("category", 100), ("published_at", 40), ("byline", 160)):
                _require_text(record, field, field, max_length)
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["published_at"]):
                raise ContentValidationError("date must use YYYY-MM-DD format.")
            try:
                date.fromisoformat(record["published_at"])
            except ValueError:
                raise ContentValidationError("date must use YYYY-MM-DD format.") from None
            _optional_url(record, "image_url")
            if "image_alt" in record and (not isinstance(record["image_alt"], str) or len(record["image_alt"]) > 300):
                raise ContentValidationError("image_alt must be text within 300 characters.")
            for field in ("published", "featured"):
                if not isinstance(record.get(field), bool):
                    raise ContentValidationError(f"{field} must be a boolean.")
            _require_order(record)
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
                raise ContentValidationError("News ids must be URL-safe slugs.")
        elif name == "opportunities":
            for field, max_length in (("title", 240), ("description", 10000), ("category", 100)):
                _require_text(record, field, field, max_length)
            for field in ("topics", "skills"):
                if not isinstance(record.get(field, []), list) or any(not isinstance(item, str) for item in record.get(field, [])):
                    raise ContentValidationError(f"{field} must be a list of strings.")
            if not isinstance(record.get("difficulty", ""), str) or len(record.get("difficulty", "")) > 80:
                raise ContentValidationError("difficulty must be text within 80 characters.")
            _optional_url(record, "image_url")
            if "image_alt" in record and (not isinstance(record["image_alt"], str) or len(record["image_alt"]) > 300):
                raise ContentValidationError("image_alt must be text within 300 characters.")
            hours = record.get("weekly_hours", 0)
            if not isinstance(hours, int) or isinstance(hours, bool) or not 0 <= hours <= 168:
                raise ContentValidationError("weekly_hours must be a whole number between 0 and 168.")
            for field in ("published", "closed", "featured"):
                if not isinstance(record.get(field), bool):
                    raise ContentValidationError(f"{field} must be a boolean.")
            _require_order(record)
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
                raise ContentValidationError("Opportunity ids must be URL-safe slugs.")
        elif name == "founders":
            _validate_person(record)
        elif name == "national_heads":
            _validate_person(record, national_head=True)
        else:
            raise ContentValidationError(f"Unknown content document '{name}'.")


class ContentRepository:
    def __init__(self, content_root, github_store=None):
        self.content_root = Path(content_root)
        self.github_store = github_store or GitHubContentStore(None, None, None, None)

    @property
    def github_read_enabled(self):
        return self.github_store.configured

    def load(self, name):
        if name not in DOCUMENT_PATHS:
            raise ContentError(f"Unknown content document '{name}'.")
        relative_path = DOCUMENT_PATHS[name]
        if self.github_read_enabled:
            result = self.github_store.read_file(relative_path)
            if result is None:
                raise ContentError(f"GitHub content file '{relative_path}' was not found.")
            raw_content = result[0]
        else:
            path = self.content_root / relative_path
            try:
                raw_content = path.read_text(encoding="utf-8")
            except OSError:
                raise ContentError(f"Content file '{relative_path}' could not be read.") from None
        try:
            value = json.loads(raw_content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ContentValidationError(f"Content file '{relative_path}' contains malformed JSON.") from None
        normalized = normalize_document(name, value)
        validate_document(name, normalized)
        return normalized

    def list_records(self, collection):
        if collection not in COLLECTIONS:
            raise ContentError(f"'{collection}' is not a content collection.")
        records = self.load(collection)
        if collection == "news":
            return sorted(records, key=lambda item: (item["order"], item["published_at"], item["title"].casefold()))
        if collection == "national_heads":
            return sorted(records, key=lambda item: (item["country"].casefold(), item["order"], item["name"].casefold()))
        return sorted(records, key=lambda item: (item["order"], item.get("title", item.get("name", "")).casefold()))

    def save_record(self, collection, record, message, current_id=None):
        if collection not in COLLECTIONS:
            raise ContentError(f"'{collection}' is not a content collection.")
        records = self.load(collection)
        record = dict(record)
        existing_index = next((index for index, item in enumerate(records) if item["id"] == current_id), None)
        if current_id is not None and existing_index is None:
            raise ContentError("The requested content item was not found.")
        if any(item["id"] == record.get("id") and item["id"] != current_id for item in records):
            raise ContentValidationError("An item with this slug already exists.")
        if existing_index is None:
            records.append(record)
        else:
            old_record = records[existing_index]
            records[existing_index] = {**old_record, **record}
        self.save(collection, records, message)
        return record

    def save(self, name, value, message):
        if name not in DOCUMENT_PATHS:
            raise ContentError(f"Unknown content document '{name}'.")
        value = normalize_document(name, value)
        validate_document(name, value)
        raw_content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        relative_path = DOCUMENT_PATHS[name]
        self.github_store.write_file(relative_path, raw_content, message)
        return value

    def delete_record(self, collection, identifier, message):
        records = self.load(collection)
        updated = [record for record in records if record["id"] != identifier]
        if len(updated) == len(records):
            raise ContentError("The requested content item was not found.")
        return self.save(collection, updated, message)

    def public_news(self):
        records = [item for item in self.load("news") if item["published"]]
        records.sort(key=lambda item: item["published_at"], reverse=True)
        records.sort(key=lambda item: item["order"])
        return records

    def public_news_item(self, slug):
        return next((item for item in self.public_news() if item["id"] == slug), None)

    def public_opportunities(self):
        records = [item for item in self.load("opportunities") if item["published"]]
        return sorted(records, key=lambda item: (not item["featured"], item["order"], item["title"].casefold()))

    def public_opportunity(self, slug):
        return next((item for item in self.public_opportunities() if item["id"] == slug), None)

    def public_people(self, collection):
        if collection not in {"founders", "national_heads"}:
            raise ContentError("Unknown people collection.")
        records = [item for item in self.load(collection) if item["active"]]
        if collection == "national_heads":
            records.sort(key=lambda item: (item["country"].casefold(), item["order"], item["name"].casefold()))
        else:
            records.sort(key=lambda item: (item["order"], item["name"].casefold()))
        return records

    def featured_news(self):
        return [item for item in self.public_news() if item["featured"]]