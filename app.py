import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit

from flask import Flask, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["APP_ENV"] = os.getenv("APP_ENV", "development").lower()
app.config["DEBUG"] = app.config["APP_ENV"] == "development" and os.getenv("FLASK_DEBUG", "0") == "1"
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///aura.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "true" if app.config["APP_ENV"] == "production" else "false").lower() == "true"
app.config["PERMANENT_SESSION_LIFETIME"] = int(os.getenv("SESSION_LIFETIME_SECONDS", "3600"))

if app.config["APP_ENV"] == "production":
    if not app.config["SECRET_KEY"] or app.config["SECRET_KEY"] in {"dev-secret-key-change-me", "replace-with-a-long-random-secret"}:
        raise RuntimeError("A strong SECRET_KEY is required in production.")
    if not os.getenv("DATABASE_URL") or app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
        raise RuntimeError("A production DATABASE_URL must be configured.")

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth_page"
login_manager.login_message = "Please sign in to access your AURA dashboard."

ALLOWED_ROLES = {"Student", "Researcher", "Developer", "Other"}
ALLOWED_INTERESTS = {
    "AI/ML",
    "Robotics",
    "Research",
    "Open-source",
    "Leadership",
}
RATE_LIMIT_WINDOW_SECONDS = 300
RATE_LIMIT_MAX_REQUESTS = 10

rate_lock = {}


def is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


def is_valid_github_url(value: str) -> bool:
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "github.com":
        return False
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return False
    return bool(re.fullmatch(r"/[A-Za-z0-9_.-]+/?", parsed.path))


def is_valid_discord(value: str) -> bool:
    value = value.strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", value))


def validate_weekly_commitment(value: str) -> bool:
    try:
        hours = int(value)
        return 1 <= hours <= 40
    except (TypeError, ValueError):
        return False


def is_safe_text(value: str, minimum: int = 1, maximum: int = 255) -> bool:
    return minimum <= len(value) <= maximum and not any(ord(char) < 32 for char in value)


def is_rate_limited(identifier: str) -> bool:
    now = time.time()
    bucket = rate_lock.setdefault(identifier, [])
    rate_lock[identifier] = [stamp for stamp in bucket if now - stamp < RATE_LIMIT_WINDOW_SECONDS]
    if len(rate_lock[identifier]) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    rate_lock[identifier].append(now)
    return False


def auth_rate_limited(mode: str, email: str = "") -> bool:
    ip = request.remote_addr or "unknown"
    if is_rate_limited(f"auth:{mode}:ip:{ip}"):
        return True
    if mode == "login" and email and is_rate_limited(f"auth:login:email:{email}"):
        return True
    return False


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    country = db.Column(db.String(120), nullable=False)
    github_profile = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    interests = db.Column(db.Text, nullable=False, default="[]")
    experience = db.Column(db.Text, nullable=False)
    contribution = db.Column(db.Text, nullable=False)
    discord_username = db.Column(db.String(64), nullable=False)
    weekly_commitment = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    @property
    def is_active(self):
        return True

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def profile(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "email": self.email,
            "country": self.country,
            "github_profile": self.github_profile,
            "role": self.role,
            "interests": self.parse_interests(self.interests),
            "experience": self.experience,
            "contribution": self.contribution,
            "discord_username": self.discord_username,
            "weekly_commitment": self.weekly_commitment,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def parse_interests(raw_value: str | None) -> list[str]:
        if not raw_value:
            return []
        try:
            import json
            parsed = json.loads(raw_value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass
        return [part.strip() for part in str(raw_value).split(",") if part.strip()]

    @staticmethod
    def serialize_interests(values: Iterable[str]) -> str:
        import json
        return json.dumps(list(values), separators=(",", ":"))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.route("/")
def home():
    return render_template("landing.html", user=current_user)


@app.route("/auth", methods=["GET", "POST"])
@app.route("/auth/<string:mode>", methods=["GET", "POST"])
def auth_page(mode=None):
    if mode is None:
        mode = request.args.get("mode", "signup")
    mode = mode if mode in {"login", "signup"} else "signup"

    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        mode = request.form.get("mode", mode)
        submitted_email = request.form.get("email", "").strip().lower()
        if auth_rate_limited(mode, submitted_email):
            flash("Too many attempts. Please wait a few minutes and try again.", "error")
            return render_template("auth.html", mode=mode, user=current_user)

        if mode == "login":
            email = submitted_email
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if not user or not user.check_password(password):
                flash("Invalid email or password.", "error")
                return render_template("auth.html", mode="login", user=current_user)
            login_user(user)
            session.permanent = True
            flash("Welcome back to AURA.", "success")
            return redirect(url_for("dashboard"))

        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        country = request.form.get("country", "").strip()
        github_profile = request.form.get("github_profile", "").strip()
        role = request.form.get("role", "").strip()
        raw_interests = request.form.getlist("interests")
        experience = request.form.get("experience", "").strip()
        contribution = request.form.get("contribution", "").strip()
        discord_username = request.form.get("discord_username", "").strip()
        weekly_commitment = request.form.get("weekly_commitment", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        errors = []
        if not is_safe_text(full_name, minimum=2, maximum=120):
            errors.append("Full name is required.")
        if not is_valid_email(email):
            errors.append("Please enter a valid email address.")
        if not is_safe_text(country, maximum=120):
            errors.append("Country is required.")
        if not github_profile or not is_valid_github_url(github_profile):
            errors.append("GitHub profile must be a valid GitHub URL.")
        if role not in ALLOWED_ROLES:
            errors.append("Please select a valid role.")
        cleaned_interests = []
        for item in raw_interests:
            if item in ALLOWED_INTERESTS and item not in cleaned_interests:
                cleaned_interests.append(item)
        if len(cleaned_interests) == 0 or len(cleaned_interests) > 2:
            errors.append("Select between 1 and 2 interest areas.")
        if not is_safe_text(experience, minimum=10, maximum=4000):
            errors.append("Please provide a brief relevant experience summary.")
        if not is_safe_text(contribution, minimum=10, maximum=4000):
            errors.append("Please tell us what you want to contribute or gain from AURA.")
        if not is_valid_discord(discord_username):
            errors.append("Discord username must contain only letters, numbers, underscores, periods, or dashes.")
        if not validate_weekly_commitment(weekly_commitment):
            errors.append("Weekly commitment must be a whole number between 1 and 40 hours.")
        if len(password) < 8 or not re.search(r"[A-Z]", password) or not re.search(r"\d", password):
            errors.append("Password must be at least 8 characters and include one uppercase letter and one number.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if User.query.filter_by(email=email).first():
            errors.append("An account with that email already exists.")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("auth.html", mode="signup", user=current_user, form_data={
                "full_name": full_name,
                "email": email,
                "country": country,
                "github_profile": github_profile,
                "role": role,
                "interests": cleaned_interests,
                "experience": experience,
                "contribution": contribution,
                "discord_username": discord_username,
                "weekly_commitment": weekly_commitment,
            })

        user = User(
            full_name=full_name,
            email=email,
            country=country,
            github_profile=github_profile,
            role=role,
            interests=User.serialize_interests(cleaned_interests),
            experience=experience,
            contribution=contribution,
            discord_username=discord_username,
            weekly_commitment=int(weekly_commitment),
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        session.permanent = True
        flash("Account created successfully. Welcome to AURA.", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth.html", mode=mode, user=current_user)


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        full_name = request.form.get("full_name", current_user.full_name).strip()
        country = request.form.get("country", current_user.country).strip()
        github_profile = request.form.get("github_profile", current_user.github_profile).strip()
        role = request.form.get("role", current_user.role).strip()
        raw_interests = request.form.getlist("interests")
        experience = request.form.get("experience", current_user.experience).strip()
        contribution = request.form.get("contribution", current_user.contribution).strip()
        discord_username = request.form.get("discord_username", current_user.discord_username).strip()
        weekly_commitment = request.form.get("weekly_commitment", str(current_user.weekly_commitment)).strip()

        errors = []
        if not is_safe_text(full_name, minimum=2, maximum=120):
            errors.append("Full name is required.")
        if not is_safe_text(country, maximum=120):
            errors.append("Country is required.")
        if not github_profile or not is_valid_github_url(github_profile):
            errors.append("GitHub profile must be a valid GitHub URL.")
        if role not in ALLOWED_ROLES:
            errors.append("Please select a valid role.")
        cleaned_interests = [item for item in raw_interests if item in ALLOWED_INTERESTS]
        if len(cleaned_interests) == 0 or len(cleaned_interests) > 2:
            errors.append("Select between 1 and 2 interest areas.")
        if not is_safe_text(experience, minimum=10, maximum=4000):
            errors.append("Please provide a concise summary of relevant experience.")
        if not is_safe_text(contribution, minimum=10, maximum=4000):
            errors.append("Please provide an AURA contribution or goal statement.")
        if not is_valid_discord(discord_username):
            errors.append("Discord username must contain only letters, numbers, underscores, periods, or dashes.")
        if not validate_weekly_commitment(weekly_commitment):
            errors.append("Weekly commitment must be a whole number between 1 and 40 hours.")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("dashboard.html", user=current_user, form_data={
                "full_name": full_name,
                "country": country,
                "github_profile": github_profile,
                "role": role,
                "interests": cleaned_interests,
                "experience": experience,
                "contribution": contribution,
                "discord_username": discord_username,
                "weekly_commitment": weekly_commitment,
            })

        current_user.full_name = full_name
        current_user.country = country
        current_user.github_profile = github_profile
        current_user.role = role
        current_user.interests = User.serialize_interests(cleaned_interests)
        current_user.experience = experience
        current_user.contribution = contribution
        current_user.discord_username = discord_username
        current_user.weekly_commitment = int(weekly_commitment)
        db.session.commit()
        flash("Profile updated successfully.", "success")

    return render_template("dashboard.html", user=current_user, form_data=current_user.profile)


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth_page"))


@app.before_request
def ensure_database_ready():
    if app.config["APP_ENV"] == "development" and not app.config.get("TESTING"):
        db.create_all()


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    return render_template("error.html", code=400, message="The form security token is invalid or missing."), 400


@app.errorhandler(400)
def handle_bad_request(error):
    return render_template("error.html", code=400, message="The request could not be processed."), 400


@app.errorhandler(404)
def handle_not_found(error):
    return render_template("error.html", code=404, message="The page you requested does not exist."), 404


@app.errorhandler(405)
def handle_method_not_allowed(error):
    return render_template("error.html", code=405, message="That method is not available here."), 405


@app.errorhandler(500)
def handle_server_error(error):
    db.session.rollback()
    app.logger.exception("Unhandled application error")
    return render_template("error.html", code=500, message="AURA could not complete that request."), 500


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], host="0.0.0.0", port=5000)
