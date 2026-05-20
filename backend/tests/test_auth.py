"""
Tests for /auth/* endpoints and auth.validate_password().

Coverage:
- Register: first user → admin, second → user, duplicate → 400
- Login: valid credentials, wrong password, unknown user
- /auth/me: happy path, no token, invalid token
- validate_password: length rule, letter rule, digit rule
"""

from auth import validate_password
from tests.conftest import bearer


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_first_registered_user_gets_admin_role(client):
    r = client.post("/auth/register", data={"username": "alice", "password": "pass1234"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_second_registered_user_gets_user_role(client):
    client.post("/auth/register", data={"username": "alice", "password": "pass1234"})
    r = client.post("/auth/register", data={"username": "bob", "password": "pass1234"})
    assert r.status_code == 200
    assert r.json()["role"] == "user"


def test_duplicate_username_returns_400(client):
    client.post("/auth/register", data={"username": "alice", "password": "pass1234"})
    r = client.post("/auth/register", data={"username": "alice", "password": "pass9999"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_returns_token_and_metadata(client):
    client.post("/auth/register", data={"username": "alice", "password": "pass1234"})
    r = client.post("/auth/login", data={"username": "alice", "password": "pass1234"})
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["username"] == "alice"
    assert data["role"] == "admin"  # first user


def test_login_wrong_password_returns_401(client):
    client.post("/auth/register", data={"username": "alice", "password": "pass1234"})
    r = client.post("/auth/login", data={"username": "alice", "password": "wrongpass"})
    assert r.status_code == 401


def test_login_nonexistent_user_returns_401(client):
    r = client.post("/auth/login", data={"username": "ghost", "password": "pass1234"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------

def test_me_returns_user_info(client, admin_token):
    r = client.get("/auth/me", headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["username"] == "admin_user"
    assert data["role"] == "admin"


def test_me_without_token_returns_401(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_with_garbage_token_returns_401(client):
    r = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# validate_password (unit tests — no HTTP)
# ---------------------------------------------------------------------------

def test_validate_password_too_short():
    assert validate_password("abc1") is not None


def test_validate_password_no_digit():
    assert validate_password("abcdefgh") is not None


def test_validate_password_no_letter():
    assert validate_password("12345678") is not None


def test_validate_password_valid_returns_none():
    assert validate_password("pass1234") is None
