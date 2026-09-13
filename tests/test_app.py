import os
import re
import subprocess
import sys

import pytest

from app import Opportunity, OpportunityInterest, User, app, db, is_valid_github_url, normalize_database_url, rate_lock


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
    assert b'ADHAYAN' in resp.data


@pytest.mark.parametrize(
    ('database_url', 'expected'),
    [
        ('postgresql://user:password@db.example.com/adhyayan', 'postgresql+psycopg://user:password@db.example.com/adhyayan'),
        ('postgres://user:password@db.example.com/adhyayan', 'postgresql+psycopg://user:password@db.example.com/adhyayan'),
        ('postgresql+psycopg://user:password@db.example.com/adhyayan', 'postgresql+psycopg://user:password@db.example.com/adhyayan'),
        ('sqlite:///adhyayan.db', 'sqlite:///adhyayan.db'),
        ('postgresql://user:password@db.example.com:5432/adhyayan', 'postgresql+psycopg://user:password@db.example.com:5432/adhyayan'),
        ('postgresql://user:password@db.example.com/adhyayan?sslmode=require&application_name=adhyayan', 'postgresql+psycopg://user:password@db.example.com/adhyayan?application_name=adhyayan&sslmode=require'),
        ('postgresql://encoded%40user:p%40ss%3Aword@db.example.com/adhyayan', 'postgresql+psycopg://encoded%40user:p%40ss%3Aword@db.example.com/adhyayan'),
    ],
)
def test_normalize_database_url(database_url, expected):
    assert normalize_database_url(database_url) == expected


def test_normalize_database_url_preserves_missing_value():
    assert normalize_database_url(None) is None


@pytest.mark.parametrize(
    'database_url',
    [
        'postgresql://user:password@db.example.com:5432/adhyayan?sslmode=require',
        'postgres://user:password@db.example.com/adhyayan',
        'postgresql+psycopg://user:password@db.example.com/adhyayan',
        'sqlite:///adhyayan.db',
    ],
)
def test_application_configuration_and_engine_use_expected_driver(database_url):
    environment = os.environ.copy()
    environment.update({
        'APP_ENV': 'development',
        'DATABASE_URL': database_url,
    })
    command = (
        'from sqlalchemy.engine import make_url; from app import app, db; '
        'app.app_context().push(); '
        'config_url = make_url(app.config["SQLALCHEMY_DATABASE_URI"]); '
        'engine_url = db.engine.url; '
        'print(config_url.drivername); '
        'print(engine_url.drivername); '
        'print(bool(engine_url.host), bool(engine_url.port), bool(engine_url.database))'
    )
    result = subprocess.run([sys.executable, '-c', command], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    expected_driver = 'sqlite' if database_url.startswith('sqlite:') else 'postgresql+psycopg'
    assert lines[0] == expected_driver
    assert lines[1] == expected_driver
    if database_url.startswith('sqlite:'):
        assert lines[2] == 'False False True'
    else:
        expected_port = ':5432' in database_url
        assert lines[2] == f'True {expected_port} True'


def test_production_rejects_missing_database_url():
    environment = os.environ.copy()
    environment.update({'APP_ENV': 'production', 'SECRET_KEY': 'test-production-secret'})
    environment['DATABASE_URL'] = ''
    result = subprocess.run([sys.executable, '-c', 'import app'], env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'A production DATABASE_URL must be configured.' in result.stderr


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
    assert b'could not create that account' in response.data.lower()
    assert b'email' not in response.data.lower() or b'account with that email' not in response.data.lower()


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


def test_opportunity_model_and_readonly_routes(client):
    with app.app_context():
        assert 'id' in Opportunity.__table__.columns.keys()
        assert 'title' in Opportunity.__table__.columns.keys()
        assert 'status' in Opportunity.__table__.columns.keys()

    signup = client.post('/auth', data={
        'full_name': 'Opportunity User',
        'email': 'opp@example.com',
        'country': 'USA',
        'github_profile': 'https://github.com/oppuser',
        'role': 'Developer',
        'interests': ['AI/ML'],
        'experience': 'Built a research tooling prototype.',
        'contribution': 'I want to work across learning systems.',
        'discord_username': 'opp_user',
        'weekly_commitment': '10',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)
    assert signup.status_code == 200

    with app.app_context():
        db.session.add_all([
            Opportunity(
                title='Open AI Lab',
                description='Help explore machine learning experiments and prototype tools.',
                category='Research',
                interests='["AI/ML","Research"]',
                difficulty='Intermediate',
                estimated_hours_per_week=8,
                required_skills='["Python","Data analysis"]',
                status='active',
            ),
            Opportunity(
                title='Closed Community Sprint',
                description='This opportunity is now closed to new users.',
                category='Community',
                interests='["Leadership"]',
                difficulty='Beginner',
                estimated_hours_per_week=4,
                required_skills='["Communication"]',
                status='closed',
            ),
        ])
        db.session.commit()

    client.post('/logout', follow_redirects=True)
    unauthenticated = client.get('/opportunities', follow_redirects=True)
    assert unauthenticated.status_code == 200
    assert b'Login' in unauthenticated.data or b'Sign Up' in unauthenticated.data

    login = client.post('/auth', data={
        'mode': 'login',
        'email': 'opp@example.com',
        'password': 'StrongPass123!'
    }, follow_redirects=True)
    assert login.status_code == 200

    authenticated = client.get('/opportunities', follow_redirects=True)
    assert authenticated.status_code == 200
    assert b'Open AI Lab' in authenticated.data
    assert b'Closed Community Sprint' not in authenticated.data

    detail = client.get('/opportunities/1', follow_redirects=True)
    assert detail.status_code == 200
    assert b'Open AI Lab' in detail.data
    assert b'RESEARCH' in detail.data or b'Research' in detail.data

    closed_detail = client.get('/opportunities/2', follow_redirects=True)
    assert closed_detail.status_code == 200
    assert b'Closed' in closed_detail.data or b'closed' in closed_detail.data

    missing = client.get('/opportunities/9999', follow_redirects=True)
    assert missing.status_code == 404

    dashboard = client.get('/dashboard', follow_redirects=True)
    assert dashboard.status_code == 200
    assert b'Discover Opportunities' in dashboard.data
    html = dashboard.data.decode('utf-8')
    assert '/opportunities' in html or 'Browse Opportunities' in html


def test_opportunity_interest_flow_and_dashboard_state(client):
    signup = client.post('/auth', data={
        'full_name': 'Interest User',
        'email': 'interest@example.com',
        'country': 'USA',
        'github_profile': 'https://github.com/interestuser',
        'role': 'Developer',
        'interests': ['AI/ML'],
        'experience': 'Built several internal tools.',
        'contribution': 'I want to contribute to experimental learning programs.',
        'discord_username': 'interest_user',
        'weekly_commitment': '8',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)
    assert signup.status_code == 200

    with app.app_context():
        db.session.add_all([
            Opportunity(
                title='AI Research Lab',
                description='Explore research prototyping and model iteration.',
                category='Research',
                interests='["AI/ML","Research"]',
                difficulty='Intermediate',
                estimated_hours_per_week=10,
                required_skills='["Python","Research"]',
                status='active',
            ),
            Opportunity(
                title='Closed Sprint',
                description='This sprint has ended for now.',
                category='Community',
                interests='["Leadership"]',
                difficulty='Beginner',
                estimated_hours_per_week=4,
                required_skills='["Communication"]',
                status='closed',
            ),
        ])
        db.session.commit()

    logout = client.post('/logout', follow_redirects=True)
    assert logout.status_code == 200

    unauthenticated_interest = client.post('/opportunities/1/interest', follow_redirects=True)
    assert unauthenticated_interest.status_code == 200
    assert b'Login' in unauthenticated_interest.data or b'Sign Up' in unauthenticated_interest.data

    login = client.post('/auth', data={
        'mode': 'login',
        'email': 'interest@example.com',
        'password': 'StrongPass123!'
    }, follow_redirects=True)
    assert login.status_code == 200

    detail_before = client.get('/opportunities/1', follow_redirects=True)
    assert detail_before.status_code == 200
    assert b'Express Interest' in detail_before.data

    interest = client.post('/opportunities/1/interest', follow_redirects=True)
    assert interest.status_code == 200
    assert b'Your interest has been recorded' in interest.data or b'interest' in interest.data.lower()

    with app.app_context():
        stored = OpportunityInterest.query.filter_by(user_id=1, opportunity_id=1).first()
        assert stored is not None
        assert stored.status == 'interested'

    detail_after = client.get('/opportunities/1', follow_redirects=True)
    assert detail_after.status_code == 200
    assert b'You\'re interested' in detail_after.data or b'Withdraw Interest' in detail_after.data

    duplicate = client.post('/opportunities/1/interest', follow_redirects=True)
    assert duplicate.status_code == 200
    assert b'already expressed interest' in duplicate.data.lower()

    closed_reject = client.post('/opportunities/2/interest', follow_redirects=True)
    assert closed_reject.status_code == 200
    assert b'closed' in closed_reject.data.lower()

    missing = client.post('/opportunities/999/interest', follow_redirects=True)
    assert missing.status_code == 404

    dashboard = client.get('/dashboard', follow_redirects=True)
    assert dashboard.status_code == 200
    assert b'My Opportunities' in dashboard.data
    assert b'AI Research Lab' in dashboard.data

    withdraw = client.post('/opportunities/1/interest/withdraw', follow_redirects=True)
    assert withdraw.status_code == 200
    assert b'withdrawn' in withdraw.data.lower()

    with app.app_context():
        stored = OpportunityInterest.query.filter_by(user_id=1, opportunity_id=1).first()
        assert stored is not None
        assert stored.status == 'withdrawn'

    empty_dashboard = client.get('/dashboard', follow_redirects=True)
    assert empty_dashboard.status_code == 200
    assert b'You haven\'t expressed interest in an opportunity yet.' in empty_dashboard.data or b'You haven\'t expressed interest' in empty_dashboard.data

    client.post('/logout', follow_redirects=True)
    second_user = client.post('/auth', data={
        'full_name': 'Second User',
        'email': 'second@example.com',
        'country': 'Canada',
        'github_profile': 'https://github.com/seconduser',
        'role': 'Researcher',
        'interests': ['Research'],
        'experience': 'Worked on cross-functional research initiatives.',
        'contribution': 'I want to apply research workflows to community projects.',
        'discord_username': 'second_user',
        'weekly_commitment': '12',
        'password': 'StrongPass123!',
        'confirm_password': 'StrongPass123!'
    }, follow_redirects=True)
    assert second_user.status_code == 200

    other_user_interest = client.post('/opportunities/1/interest', follow_redirects=True)
    assert other_user_interest.status_code == 200
    assert b'Your interest has been recorded' in other_user_interest.data or b'interest' in other_user_interest.data.lower()

    client.post('/logout', follow_redirects=True)
    client.post('/auth', data={'mode': 'login', 'email': 'interest@example.com', 'password': 'StrongPass123!'}, follow_redirects=True)
    unauthorized_withdraw = client.post('/opportunities/1/interest/withdraw', follow_redirects=True)
    assert unauthorized_withdraw.status_code == 200
    assert b'You do not have an active interest' in unauthorized_withdraw.data or b'active interest' in unauthorized_withdraw.data.lower()

    client.post('/logout', follow_redirects=True)
    client.post('/auth', data={'mode': 'login', 'email': 'second@example.com', 'password': 'StrongPass123!'}, follow_redirects=True)
    own_withdraw = client.post('/opportunities/1/interest/withdraw', follow_redirects=True)
    assert own_withdraw.status_code == 200
    assert b'withdrawn' in own_withdraw.data.lower()

    client.post('/logout', follow_redirects=True)
    client.post('/auth', data={'mode': 'login', 'email': 'interest@example.com', 'password': 'StrongPass123!'}, follow_redirects=True)
    detail_while_withdrawn = client.get('/opportunities/1', follow_redirects=True)
    assert detail_while_withdrawn.status_code == 200
    assert b'Express Interest' in detail_while_withdrawn.data


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
        'contribution': 'I want to support ADHAYAN research.',
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
    assert is_valid_github_url('https://github.com/adhyayan-user')
    assert not is_valid_github_url('https://github.com.evil.example/adhyayan-user')
    assert not is_valid_github_url('https://github.com/adhyayan-user?next=https://evil.example')
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
    assert b'ADHAYAN could not complete that request' in response.data
    assert b'internal test detail' not in response.data


def test_production_requires_secret_and_disables_debug():
    environment = os.environ.copy()
    environment.update({'APP_ENV': 'production', 'SECRET_KEY': '', 'DATABASE_URL': 'postgresql://user:pass@localhost/adhyayan'})
    result = subprocess.run([sys.executable, '-c', 'import app'], env=environment, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'SECRET_KEY is required' in result.stderr


def test_migrations_are_available():
    assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', 'env.py'))
    assert os.listdir(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'migrations', 'versions'))
