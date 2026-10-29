"""
MI portal SSO answers with where "ออกจากระบบ" should take the user back to
(MI_PORTAL_URL); blank → None, so the frontend uses its own login page. Offline.
"""

import pytest
from starlette.testclient import TestClient

import main
import mi_auth


@pytest.fixture
def sso(monkeypatch):
    monkeypatch.setattr(mi_auth, "mi_sso_enabled", lambda: True)
    monkeypatch.setattr(mi_auth, "decode_mi_sso_token", lambda t: {"emp_id": "2690028", "user_expired": 0})
    monkeypatch.setattr(main, "_ensure_mi_user_provisioned",
                        lambda emp: {"id": 7, "username": emp, "role": "user", "is_disabled": False})
    monkeypatch.setattr(main, "audit_log", lambda *a, **kw: None)

    def post():
        return TestClient(main.app).post("/auth/mi-sso", json={"token": "t"})

    return post


def test_return_url_is_the_mi_portal(monkeypatch, sso):
    monkeypatch.setattr(main, "MI_PORTAL_URL", "http://192.168.5.40/AUTHEN/")
    body = sso().json()
    assert body["return_url"] == "http://192.168.5.40/AUTHEN/" and body["username"] == "2690028"


def test_no_portal_set_means_our_login_page(monkeypatch, sso):
    monkeypatch.setattr(main, "MI_PORTAL_URL", "")
    assert sso().json()["return_url"] is None
