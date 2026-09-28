# ADHAYAN — Architecture & Platform Report

**Project:** ADHAYAN
**Repository:** `https://github.com/peterkandel/AURA`
**Primary branch:** `main`
**Final release commit:** `e28e442` — `Finalize ADHAYAN Stage 3 visual polish`
**Status:** Production deployment complete and live
**Report date:** September 29, 2026

---

## 1. Executive Summary

ADHAYAN is an organizational/public-facing web platform rebuilt around a lightweight, Git-backed content-management architecture.

The rebuild deliberately moved the project away from an account-driven, database-dependent application model and toward a simpler architecture appropriate for an organization whose public visitors primarily consume information rather than maintain personal accounts.

The resulting platform has:

* A public website for organizational information, people, news, and opportunities.
* A single protected administrative identity for content management.
* JSON content documents as the canonical CMS content model.
* GitHub as the production persistence layer through the GitHub Contents API.
* Local content fallback for development/resilience.
* Flask as the application layer and content-service boundary.
* A responsive editorial/technology-oriented visual system.
* No public signup, user profiles, dashboards, or user-specific opportunity state.
* No production dependency on the legacy SQLAlchemy/PostgreSQL application model.
* Ordinary public content editable through the admin interface rather than through source-code changes.

The final release passed the project's automated test suite and repository diff checks, was pushed successfully to `main`, and was subsequently confirmed live in production.

---

# 2. Product Purpose and Scope

ADHAYAN is designed as an organizational platform rather than a social network or user-account application.

## Public Audience

Public visitors can access:

* Homepage
* About information
* News and announcements
* Individual news articles
* Opportunities / initiatives
* Individual opportunity details
* Organizational people information

Visitors do **not** need an account.

## Administrative Audience

The platform provides one shared administrative identity for managing the organization's published content.

The intended administrative username is:

`admin`

The administrator can manage ordinary site content through the protected admin interface without editing Python, HTML, raw JSON, database records, or Git commits manually.

---

# 3. Final Architecture

The final runtime architecture is intentionally small.

```text
                         PUBLIC VISITOR
                              |
                              v
                        Flask Web App
                              |
              +---------------+---------------+
              |                               |
              v                               v
       Public Templates                 Content Service
                                              |
                              +---------------+---------------+
                              |                               |
                              v                               v
                       GitHub Contents API              Local Content
                         (production)                     (fallback)
                              |
                              v
                    Git-backed JSON documents
```

The administrative flow is:

```text
                         ADMINISTRATOR
                              |
                              v
                     /admin/login
                              |
                              v
                    Session-authenticated
                        admin interface
                              |
                              v
                     Admin CRUD / Settings
                              |
                              v
                       Content Service
                              |
                              v
                    GitHub Contents API
                              |
                              v
                  Repository content/*.json
```

The important architectural boundary is the **content service**. Templates and routes do not need to know how content is persisted. The application asks the content layer for site/about/news/opportunity/people documents, while the persistence mechanism handles GitHub or local fallback.

---

# 4. Technology Stack

## Application

* Python
* Flask
* Jinja templates
* HTML
* CSS
* JavaScript for lightweight client-side interactions

## Content

Canonical content is stored as JSON documents:

```text
content/
├── site.json
├── about.json
├── news.json
├── opportunities.json
└── people/
    ├── founders.json
    └── national_heads.json
```

## Production Persistence

* GitHub repository
* GitHub Contents API
* Fine-grained GitHub token with repository-specific Contents read/write permission

## Deployment

* Render Web Service
* Gunicorn
* Production environment variables
* Automatic deployment from the `main` branch

## Testing

* Pytest
* Repository diff validation
* Responsive/browser verification
* Accessibility-oriented checks
* Reduced-motion checks
* Authentication and protected-route checks

---

# 5. Content and Persistence Architecture

## 5.1 Canonical Content Documents

The ordinary website content is represented by the following canonical documents.

### `content/site.json`

Contains global site settings and shared copy, including items such as:

* Navigation CTA label
* Page eyebrow labels
* News labels
* Opportunity labels
* Shared UI terminology
* Homepage empty-state copy
* Other editable site-level presentation text

Examples of canonical settings include:

```text
nav_cta_label
about_page_eyebrow
news_page_eyebrow
opportunities_page_eyebrow
news_home_read_label
home_news_empty_message
news_detail_back_label
opportunity_more_info_label
opportunity_open_label
opportunity_closed_label
opportunity_detail_back_label
opportunity_difficulty_label
opportunity_expected_time_label
opportunity_skills_label
weekly_hours_suffix
about_people_count_label
about_countries_count_label
```

### `content/about.json`

Contains the organization's About-page content.

### `content/news.json`

Contains published news and announcement content.

### `content/opportunities.json`

Contains opportunities / initiative records.

### `content/people/founders.json`

Contains founder information.

### `content/people/national_heads.json`

Contains national-head information.

---

## 5.2 Why GitHub Was Chosen as Persistence

The project intentionally avoids making a production relational database the canonical CMS store.

The Git-backed model provides:

* Human-readable content.
* Version history through Git.
* A simple backup mechanism.
* Direct visibility into content changes.
* No need to design and maintain a second production content database.
* Easy portability.
* A natural fit for relatively structured organizational content.

The application therefore treats GitHub-backed JSON documents as the canonical content source in production.

---

## 5.3 Local Fallback

Development can operate against local content files rather than requiring production GitHub persistence.

This provides:

* Easier local development.
* Less dependence on network/API availability during development.
* A predictable development environment.
* A fallback path when GitHub persistence is unavailable or intentionally not configured.

Production configuration explicitly requires the administrative and GitHub persistence settings necessary for the live deployment.

---

# 6. Administrative Architecture

## Authentication Model

The platform intentionally uses a **single shared administrator identity**.

The design does not attempt to create a multi-user permissions system because that would add complexity without being required by the current product.

Administrative credentials are environment-configured.

The password is never hardcoded into the application source.

Relevant production configuration includes:

```text
APP_ENV=production
SECRET_KEY=<secret>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<secret>
GITHUB_TOKEN=<secret>
GITHUB_OWNER=peterkandel
GITHUB_REPO=AURA
GITHUB_BRANCH=main
GITHUB_CONTENT_ROOT=content
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

Sensitive values are supplied through the deployment environment rather than committed to Git.

---

## Administrative Routes

The architecture includes:

```text
/admin
/admin/login
/admin/logout
```

along with administrative collection/settings interfaces.

Unauthenticated users attempting to access protected admin routes are redirected to the login flow.

---

## CMS Capabilities

The admin interface supports ordinary content management rather than merely exposing read-only settings.

The intended workflow is:

1. Administrator signs in.
2. Administrator opens the relevant collection/settings page.
3. Administrator edits content through the form.
4. The application validates/normalizes the submitted data.
5. The content service persists the updated JSON document.
6. The public site reads the updated canonical content.
7. No manual code edit or redeployment is required for ordinary content changes.

---

# 7. Public Architecture

The public site is deliberately account-free.

Primary public routes are:

```text
/
/about
/news
/news/<article>
/opportunities
/opportunities/<opportunity>
```

The public architecture separates:

* Routing
* Content retrieval
* Content normalization
* Template rendering
* Visual presentation

This keeps the content model independent of the page implementation.

---

# 8. Visual and UX Direction

The final visual direction was a substantial redesign rather than a simple recoloring.

The target was a sophisticated editorial/technology organization website with a distinctive identity.

Inspirational references included the general qualities of sites such as Ellipsus and AI Nepal, but the final implementation was not intended to clone either site.

## Visual System

The redesign established:

* Deep evergreen / dark foundation
* Warm paper-like surfaces
* Acid-lime, coral, and gold accent usage
* Expressive typography
* Strong editorial hierarchy
* Defined spacing system
* Structured borders
* Controlled shadows
* Consistent radii
* Reusable cards
* Shared form/control styling
* Clear interaction states

## Public Page Redesign

The following pages received dedicated structural redesigns:

* Homepage
* About
* News archive
* News detail
* Opportunities archive
* Opportunity detail

The homepage was treated as a distinct editorial landing experience rather than simply a collection of generic sections.

News and opportunities were structured around content discovery, hierarchy, and clear calls to action.

People-related presentation was designed to feel organizational/editorial rather than like a generic database table.

## Admin Redesign

The admin dashboard and collection interfaces were also redesigned so that they belong to the same product system rather than appearing as an unrelated legacy interface.

Shared form, button, card, focus, spacing, and surface treatments were established in the global stylesheet.

---

# 9. Responsive Design

The final implementation was checked across the following viewport widths:

```text
1440px
1024px
768px
640px
390px
320px
```

The public route matrix was checked for horizontal overflow.

A concrete mobile issue was found during final Stage 3 verification:

* `/news` at a 390px viewport had a heading wider than the effective layout viewport.
* The problem was isolated to `.newsroom-header h1`.
* The existing narrow-screen rule did not activate at the browser's effective 375px media viewport.
* The smallest appropriate correction was extending the narrow-screen media rule to `400px`.

After the fix, the relevant viewport measured:

```text
clientWidth: 375
scrollWidth: 375
```

with no horizontal overflow.

---

# 10. Accessibility and Interaction Details

The final implementation includes several accessibility-oriented behaviors.

## Navigation

* Mobile navigation opens and closes correctly.
* `aria-expanded` reflects the mobile navigation state.
* Current navigation state uses `aria-current`.

## Keyboard Interaction

Visible keyboard focus states were checked.

## Forms

Administrative login fields have labels and appropriate autocomplete behavior.

## Semantics

Homepage heading hierarchy and primary interactive elements were checked.

## Reduced Motion

A reduced-motion mode was verified so that animations/transitions are reduced or disabled for users who request reduced motion.

## Responsive Interaction

Mobile-specific behavior was checked rather than assuming desktop interactions would simply scale down.

---

# 11. Content Editing and CMS Philosophy

One of the major architectural goals was to eliminate the need for technical intervention for ordinary content changes.

The following principle governs the platform:

> If an ordinary administrator should reasonably be able to change it, it should live in editable CMS content rather than being hardcoded in templates.

This motivated the migration of shared public copy into `site.json`.

For example, the following text is represented as editable settings rather than independent hardcoded strings:

* `News & Announcements`
* `Read update`
* `All news`
* `More information`
* `Open`
* `Closed`
* `All initiatives`
* `Difficulty`
* `Expected time`
* `Skills`
* `hrs/week`
* `people`
* `countries`

This makes wording and terminology changes possible through the CMS.

---

# 12. Major Changes From the Earlier Architecture

The rebuild made several significant architectural decisions.

## 12.1 Removed Runtime Dependence on the Old Database Architecture

The earlier project had an application model involving:

* SQLAlchemy
* Flask-Login
* Flask-Migrate/Alembic
* PostgreSQL/SQLite-oriented database configuration

The rebuilt runtime does not import or depend on those systems for ordinary operation.

Legacy database packages/migrations may remain in the repository as dormant historical artifacts, but they are not part of the active content architecture.

Importantly, the production PostgreSQL state was not dropped, migrated, or destructively modified merely to perform the rebuild.

---

## 12.2 Removed Public Account Functionality

The product direction explicitly rejected:

* Public signup
* Public login
* User profiles
* User dashboards
* User-specific opportunity state

This significantly reduces application complexity and attack surface.

---

## 12.3 Replaced Database-Backed CMS Persistence

Ordinary CMS content moved from a database-oriented concept to Git-backed JSON documents.

This provides a more transparent and portable content model for the organization's current scale and needs.

---

## 12.4 Introduced a Content-Service Boundary

The content service became the architectural boundary between the Flask application and content storage.

This allows the application to use:

* GitHub-backed persistence in production.
* Local content during development.

without forcing templates or route handlers to understand the persistence mechanism.

---

## 12.5 Centralized Editable Site Copy

Shared site copy was moved into site settings rather than scattered across templates.

The canonical example is the final capitalization:

```text
News & Announcements
```

This is represented in both the canonical site content and the development fallback defaults.

---

# 13. Testing and Verification

The final application passed:

```text
41 tests passed
```

The final verification also included:

* `git diff --check`
* Responsive route checks
* Public route HTTP checks
* Mobile navigation behavior
* `aria-expanded` behavior
* Keyboard focus visibility
* Semantic heading checks
* Interactive-label checks
* Admin login label/autocomplete checks
* Reduced-motion verification
* Protected admin route behavior
* Horizontal-overflow checks

The final Stage 3 source diff was intentionally small.

It changed exactly:

```text
content/site.json
content_service.py
static/site.css
```

The final Stage 3 diff contained:

```text
5 insertions
5 deletions
```

No secrets, `.env` files, debug configuration, database migrations, or dependency changes were introduced by the final visual polish.

---

# 14. Git and Release State

The rebuild was committed in controlled stages.

A major rebuild commit was:

```text
63d1295
Rebuild ADHAYAN around Git-backed CMS
```

The final release commit was:

```text
e28e442
Finalize ADHAYAN Stage 3 visual polish
```

The final commit was pushed to:

```text
origin/main
```

Repository:

```text
https://github.com/peterkandel/AURA.git
```

The final Git state was clean and synchronized with the remote.

No force push was used.

---

# 15. Deployment Architecture

The production application runs as a Render Web Service.

The deployment configuration was simplified from the earlier database-oriented build process.

## Final Build Command

```text
pip install -r requirements.txt
```

## Final Start Command

```text
gunicorn app:app
```

The previous production build command involving:

```text
flask --app app db upgrade
```

was removed because the active architecture no longer requires database migrations to start the application.

---

# 16. Production Environment

Production requires configuration for:

* Flask production mode
* Secret session/security key
* Admin credentials
* GitHub token
* GitHub repository owner
* GitHub repository
* Git branch
* Content root
* Secure session cookies

The GitHub token is a fine-grained token scoped to the intended repository with Contents read/write access.

The token itself is not stored in this report.

---

# 17. Security Model

The final platform's security model is intentionally narrow.

## Authentication

Only the administrative interface requires authentication.

## Credential Storage

Admin credentials are environment-configured.

## Session Security

Production is configured for:

```text
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
```

## GitHub Credentials

The GitHub token is supplied through production environment configuration rather than source control.

## Public Attack Surface

By removing public accounts and user-specific application state, the platform avoids unnecessary authentication, authorization, password-reset, profile, and user-data functionality.

---

# 18. Known Limitations and Risks

The final implementation is live and functional, but several architectural limitations should be recognized.

## 18.1 Single Administrator Identity

The current CMS uses one shared administrative identity.

This is appropriate for the present operating model but does not provide:

* Per-user administrator accounts
* Role-based permissions
* Individual audit attribution
* Granular content permissions

If the organization later requires multiple editors, authentication and authorization should be redesigned rather than incrementally overloading the current shared-credential model.

---

## 18.2 GitHub as CMS Storage

GitHub is a deliberate persistence choice, but it introduces dependencies on:

* GitHub API availability
* Token validity
* Repository permissions
* API behavior/rate limits
* Repository history as the content versioning system

For the current content volume this is a reasonable architectural tradeoff, but a substantially larger or more transactional CMS may eventually justify a dedicated persistence layer.

---

## 18.3 JSON Content Model

JSON is simple and transparent but does not inherently provide the relational constraints or transactional guarantees of a database.

Validation and normalization therefore remain important at the application boundary.

---

## 18.4 Production Content Editing Should Be Monitored

A future maintenance workflow should periodically confirm that:

1. Admin login works.
2. A harmless content edit can be saved.
3. The GitHub content document is updated.
4. The public page reflects the change.
5. Invalid content is rejected cleanly.

This is especially useful after changes to GitHub permissions, Render environment variables, or the content schema.

---

## 18.5 Populated-Content Visual Coverage

During local visual verification, some collections were empty, so the visual appearance of every populated News/Opportunity/People card state could not be exhaustively reviewed in the local environment.

The structural and responsive behavior was verified, but future content additions should still receive a quick visual review.

---

# 19. Maintenance Guidelines

## For Ordinary Content Changes

Use the admin interface.

Do not manually edit:

* Python files
* Jinja templates
* CSS
* raw production JSON

unless the change is actually a software/content-schema change.

## For Software Changes

1. Work in a development environment.
2. Run the full test suite.
3. Run `git diff --check`.
4. Review the exact diff.
5. Verify responsive behavior for UI changes.
6. Commit only the intended files.
7. Push to `main` only after explicit release approval.
8. Confirm Render deployment.
9. Smoke-test the live site.

## For CMS Schema Changes

Treat schema changes as application changes, not ordinary content edits.

Update:

* normalization/validation
* fallback defaults
* admin forms
* templates
* tests
* canonical content documents

together.

---

# 20. Recommended Future Improvements

These are not requirements for the completed release.

## Short-Term

* Add a repeatable populated-content fixture set for visual regression testing.
* Add a production smoke-test checklist to the repository.
* Consider automated checks for the GitHub persistence path.
* Expand automated accessibility assertions where useful.

## Medium-Term

* Add content preview/draft support if editorial workflow becomes more complex.
* Add richer validation for people/news/opportunity documents.
* Consider image/media management if the site's content volume grows.

## Long-Term

If ADHAYAN evolves into a multi-editor platform, consider:

* Individual administrator accounts
* Role-based access control
* Audit logs
* Draft/publish states
* Dedicated media storage
* More structured content management
* Dedicated database persistence if transactional requirements justify it

These should be introduced in response to actual product requirements rather than added preemptively.

---

# 21. Final Project State

At the end of the rebuild:

```text
Architecture:          Git-backed CMS + Flask
Public accounts:       None
Admin accounts:        Single shared identity
Content storage:       GitHub JSON documents
Development fallback:  Local JSON content
Production host:       Render
Application server:    Gunicorn
Public UI:             Editorial/technology-oriented redesign
Responsive:             Verified across desktop and mobile widths
Accessibility:          Core interaction checks completed
Tests:                  41 passed
Diff check:             Passed
Git state:              Clean
Release branch:         main
Final release:          e28e442
Production:             Live and verified
```

---

# 22. Final Acceptance Summary

ADHAYAN's rebuild is complete.

The project now has a coherent separation between:

```text
Presentation
    ↓
Flask application
    ↓
Content service
    ↓
GitHub-backed content
```

rather than coupling the public website to the legacy database/application architecture.

The final release preserved the necessary administrative functionality while substantially simplifying the public product model.

The visual redesign was implemented across the public site and administrative surfaces, responsive behavior was tested across the target viewport matrix, the final mobile overflow issue was corrected, the site-wide editable copy model was completed, automated tests remained green, and the final release was committed and pushed.

The application is now live in production.

---

# 23. Appendix — Canonical Content Tree

```text
content/
├── site.json
├── about.json
├── news.json
├── opportunities.json
└── people/
    ├── founders.json
    └── national_heads.json
```

# Appendix — Primary Runtime Surface

```text
Public
├── /
├── /about
├── /news
├── /news/<article>
├── /opportunities
└── /opportunities/<opportunity>

Admin
├── /admin/login
├── /admin
└── /admin/logout
```

# Appendix — Final Release

```text
Repository:
https://github.com/peterkandel/AURA

Branch:
main

Final commit:
e28e442

Commit message:
Finalize ADHAYAN Stage 3 visual polish

Test result:
41 passed

Deployment:
Production live
```

---

**End of ADHAYAN Architecture & Platform Report**
