import os
import re
import subprocess
import sys

import pytest

from app import User, app, db, is_valid_github_url, rate_lock


@pytest.fixture
def client():
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')
    rate_lock.clear()
    with app.app_context():
        db.create_all()
    with app.test_client() as client:
        yield client
    with app.app_context():
        db.drop_all()


def test_homepage_loads(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert b'AURA' in resp.data


def test_auth_routes_load_and_modes(client):
    assert client.get('/auth').status_code == 200
    signup_resp = client.get('/auth/signup')
    login_resp = client.get('/auth/login')
    assert signup_resp.status_code == 200
    assert b'Create account' in signup_resp.data
    assert b'Login' in signup_resp.data
    assert login_resp.status_code == 200
    assert b'Login' in login_resp.data
    assert b'Create account' not in login_resp.data


def test_signup_and_login_flow(client):
    payload = {
        'full_name': 'Test User',
        'email': 'test@example.com',
        'country': 'United States',
        'github_profile': 'https://github.com/testuser',
        'role': 'Developer',
        'interests': ['AI/ML', 'Open-source'],
        'experience': 'Built a small ML project',
        'contribution': 'I want to contribute to research tooling.',
        'discord_username': 'test_user',
        'weekly_commitment': '12',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }
    signup = client.post('/auth', data=payload, follow_redirects=True)
    assert signup.status_code == 200
    assert b'Welcome back' in signup.data or b'Dashboard' in signup.data

    login = client.post('/auth', data={
        'mode': 'login',
        'email': 'test@example.com',
        'password': 'StrongPass123!'
    }, follow_redirects=True)
    assert login.status_code == 200
    assert b'Dashboard' in login.data or b'Profile' in login.data


def test_duplicate_email_rejected(client):
    client.post('/auth', data={
        'full_name': 'First User',
        'email': 'dup@example.com',
        'country': 'Canada',
        'github_profile': 'https://github.com/firstuser',
        'role': 'Developer',
        'interests': ['AI/ML'],
        'experience': 'Worked on research tooling.',
        'contribution': 'I want to contribute to open-source.',
        'discord_username': 'first_user',
        'weekly_commitment': '10',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)

    client.post('/logout', follow_redirects=True)
    response = client.post('/auth', data={
        'full_name': 'Second User',
        'email': 'dup@example.com',
        'country': 'Canada',
        'github_profile': 'https://github.com/seconduser',
        'role': 'Researcher',
        'interests': ['Research'],
        'experience': 'Built a data pipeline.',
        'contribution': 'I want to learn from the community.',
        'discord_username': 'second_user',
        'weekly_commitment': '8',
        'password': 'AnotherPass123!',
        'confirm_password': 'AnotherPass123!'
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'already exists' in response.data.lower()


def test_invalid_login_and_signup_inputs_rejected(client):
    invalid_signup = client.post('/auth', data={
        'full_name': 'Bad',
        'email': 'not-an-email',
        'country': '',
        'github_profile': 'https://example.com',
        'role': 'Student',
        'interests': ['AI/ML', 'Robotics', 'Research'],
        'experience': '',
        'contribution': '',
        'discord_username': 'bad username',
        'weekly_commitment': '0',
        'password': 'weak',
        'confirm_password': 'weak'
    }, follow_redirects=True)
    assert invalid_signup.status_code == 200
    assert b'Please' in invalid_signup.data or b'valid' in invalid_signup.data.lower() or b'Select' in invalid_signup.data

    wrong_password = client.post('/auth', data={
        'mode': 'login',
        'email': 'notfound@example.com',
        'password': 'WrongPass123!'
    }, follow_redirects=True)
    assert wrong_password.status_code == 200
    assert b'Invalid email or password' in wrong_password.data


def test_protected_dashboard_is_blocked_without_login(client):
    resp = client.get('/dashboard', follow_redirects=True)
    assert resp.status_code == 200
    assert b'Login' in resp.data or b'Sign Up' in resp.data


def test_authenticated_dashboard_access_and_logout(client):
    client.post('/auth', data={
        'full_name': 'Session User',
        'email': 'session@example.com',
        'country': 'USA',
        'github_profile': 'https://github.com/sessionuser',
        'role': 'Developer',
        'interests': ['AI/ML'],
        'experience': 'Built a forecasting model.',
        'contribution': 'I want to collaborate on AI tooling.',
        'discord_username': 'session_user',
        'weekly_commitment': '14',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)

    logged_in = client.get('/dashboard', follow_redirects=True)
    assert logged_in.status_code == 200
    assert b'Session User' in logged_in.data

    logout_resp = client.post('/logout', data={'csrf_token': ''}, follow_redirects=True)
    assert logout_resp.status_code == 200
    assert b'signed out' in logout_resp.data.lower()

    protected_after_logout = client.get('/dashboard', follow_redirects=True)
    assert protected_after_logout.status_code == 200
    assert b'Login' in protected_after_logout.data or b'Sign Up' in protected_after_logout.data


def test_user_isolation(client):
    user_one = {
        'full_name': 'Alice Alpha',
        'email': 'alice@example.com',
        'country': 'USA',
        'github_profile': 'https://github.com/alicealpha',
        'role': 'Researcher',
        'interests': ['AI/ML'],
        'experience': 'Researching generative systems.',
        'contribution': 'I want to contribute to research design.',
        'discord_username': 'alice_alpha',
        'weekly_commitment': '12',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }
    user_two = {
        'full_name': 'Bob Beta',
        'email': 'bob@example.com',
        'country': 'UK',
        'github_profile': 'https://github.com/bobbeta',
        'role': 'Developer',
        'interests': ['Robotics'],
        'experience': 'Built a robot control stack.',
        'contribution': 'I want to help with robotics experiments.',
        'discord_username': 'bob_beta',
        'weekly_commitment': '6',
        'password': 'AnotherPass123!',
        'confirm_password': 'AnotherPass123!'
    }

    client.post('/auth', data=user_one, follow_redirects=True)
    client.post('/logout', follow_redirects=True)
    client.post('/auth', data=user_two, follow_redirects=True)

    client.post('/logout', follow_redirects=True)
    client.post('/auth', data={'mode': 'login', 'email': 'alice@example.com', 'password': 'StrongPass123!'}, follow_redirects=True)
    dashboard = client.get('/dashboard')
    assert b'Alice Alpha' in dashboard.data
    assert b'Bob Beta' not in dashboard.data

    client.post('/logout', follow_redirects=True)
    client.post('/auth', data={'mode': 'login', 'email': 'bob@example.com', 'password': 'AnotherPass123!'}, follow_redirects=True)
    dashboard_two = client.get('/dashboard')
    assert b'Bob Beta' in dashboard_two.data
    assert b'Alice Alpha' not in dashboard_two.data


def test_password_requirements_and_mismatch_rejected(client):
    weak = client.post('/auth', data={
        'full_name': 'Weak User',
        'email': 'weak@example.com',
        'country': 'Germany',
        'github_profile': 'https://github.com/weakuser',
        'role': 'Student',
        'interests': ['Leadership'],
        'experience': 'Worked on student projects.',
        'contribution': 'I want to explore community leadership.',
        'discord_username': 'weak_user',
        'weekly_commitment': '5',
        'password': 'weak',
        'confirm_password': 'weak'
    }, follow_redirects=True)
    assert weak.status_code == 200
    assert b'Password must' in weak.data or b'uppercase' in weak.data.lower() or b'number' in weak.data.lower()

    mismatch = client.post('/auth', data={
        'full_name': 'Mismatch User',
        'email': 'mismatch@example.com',
        'country': 'France',
        'github_profile': 'https://github.com/mismatchuser',
        'role': 'Other',
        'interests': ['Research'],
        'experience': 'Largely theoretical work.',
        'contribution': 'I want to support AURA research.',
        'discord_username': 'mismatch_user',
        'weekly_commitment': '9',
        'password': 'StrongPass123!',
        'confirm_password': 'DifferentPass123!'
    }, follow_redirects=True)
    assert mismatch.status_code == 200
    assert b'Passwords do not match' in mismatch.data or b'Passwords' in mismatch.data


def test_invalid_role_and_url_and_commits_are_rejected(client):
    resp = client.post('/auth', data={
        'full_name': 'Invalid User',
        'email': 'invalid@example.com',
        'country': 'Japan',
        'github_profile': 'https://notgithub.com/invalid',
        'role': 'UnknownRole',
        'interests': ['AI/ML', 'Robotics'],
        'experience': 'Created a prototype for a thing.',
        'contribution': 'I want to be part of the mission.',
        'discord_username': 'invalid_user',
        'weekly_commitment': '60',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b'valid role' in resp.data.lower() or b'GitHub profile' in resp.data.lower() or b'Weekly commitment' in resp.data.lower()


def test_csrf_rejects_missing_token_and_accepts_valid_token(client):
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        page = client.get('/auth/login')
        token = re.search(rb'type="hidden" name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        missing = client.post('/auth/login', data={'mode': 'login', 'email': 'a@example.com', 'password': 'WrongPass123!'})
        valid = client.post('/auth/login', data={'mode': 'login', 'email': 'a@example.com', 'password': 'WrongPass123!', 'csrf_token': token})
        assert missing.status_code == 400
        assert b'security token' in missing.data
        assert valid.status_code == 200
        assert b'Invalid email or password' in valid.data
    finally:
        app.config['WTF_CSRF_ENABLED'] = False


def test_security_headers_and_cookie_configuration(client):
    response = client.get('/auth')
    assert response.headers['Content-Security-Policy'].startswith("default-src 'self'")
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert 'geolocation=()' in response.headers['Permissions-Policy']
    assert app.config['SESSION_COOKIE_HTTPONLY'] is True
    assert app.config['SESSION_COOKIE_SECURE'] is False
    assert app.config['SESSION_COOKIE_SAMESITE'] == 'Lax'
    assert app.permanent_session_lifetime.total_seconds() == 3600


def test_login_rate_limit_covers_repeated_password_attempts(client):
    responses = [client.post('/auth/login', data={'mode': 'login', 'email': 'target@example.com', 'password': 'WrongPass123!'}) for _ in range(11)]
    assert all(response.status_code == 200 for response in responses)
    assert b'Too many attempts' in responses[-1].data
    assert b'Invalid email or password' in responses[0].data


def test_signup_rate_limit_covers_repeated_attempts(client):
    responses = [client.post('/auth/signup', data={'mode': 'signup'}) for _ in range(11)]
    assert all(response.status_code == 200 for response in responses)
    assert b'Too many attempts' in responses[-1].data


def test_profile_payload_is_escaped_and_github_validation_rejects_malicious_urls(client):
    assert is_valid_github_url('https://github.com/aura-user')
    assert not is_valid_github_url('https://github.com.evil.example/aura-user')
    assert not is_valid_github_url('https://github.com/aura-user?next=https://evil.example')
    payload = {
        'full_name': '<script>alert(1)</script>',
        'email': 'xss@example.com',
        'country': 'USA',
        'github_profile': 'https://github.com/xss-user',
        'role': 'Developer',
        'interests': ['AI/ML'],
        'experience': 'Built a safe profile test.',
        'contribution': 'I want to test escaped profile content.',
        'discord_username': 'safe_user',
        'weekly_commitment': '5',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }
    response = client.post('/auth', data=payload, follow_redirects=True)
    assert response.status_code == 200
    assert b'<script>alert(1)</script>' not in response.data
    assert b'&lt;script&gt;alert(1)&lt;/script&gt;' in response.data


def test_error_pages_do_not_leak_details(client):
    not_found = client.get('/does-not-exist')
    method_not_allowed = client.delete('/auth')
    assert not_found.status_code == 404
    assert method_not_allowed.status_code == 405
    assert b'page you requested does not exist' in not_found.data
    assert b'debugger' not in not_found.data.lower()


def test_server_error_page_is_generic(client):
    def raise_error():
        raise RuntimeError('internal test detail')

    original_home = app.view_functions['home']
    app.view_functions['home'] = raise_error
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.get('/')
    finally:
        app.view_functions['home'] = original_home
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 500
    assert b'AURA could not complete that request' in response.data
    assert b'internal test detail' not in response.data


def test_production_requires_secret_and_disables_debug():
    environment = os.environ.copy()
    environment.update({'APP_ENV': 'production', 'SECRET_KEY': '', 'DATABASE_URL': 'postgresql://user:pass@localhost/aura'})
    result = subprocess.run([sys.executable, '-c', 'import app'], env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'SECRET_KEY is required' in result.stderr


def test_migrations_are_available():
    assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', 'env.py'))
    assert os.listdir(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', 'versions'))
