"""V5 UI rollout flag: which chat page a user gets. Offline."""

import pytest
from starlette.testclient import TestClient

import main


@pytest.mark.parametrize("mode,users,user,expected", [
    ("off", set(), {"id": 1, "role": "admin", "username": "a"}, False),
    ("admin", set(), {"id": 1, "role": "admin", "username": "a"}, True),
    ("admin", set(), {"id": 2, "role": "user", "username": "2690028"}, False),
    ("admin", {"2690028"}, {"id": 2, "role": "user", "username": "2690028"}, True),
    ("all", set(), {"id": 3, "role": "user", "username": "x"}, True),
])
def test_ui_v5_flag(monkeypatch, mode, users, user, expected):
    monkeypatch.setattr(main, "UI_V5_MODE", mode)
    monkeypatch.setattr(main, "UI_V5_USERS", users)
    main.app.dependency_overrides[main.current_user] = lambda: user
    try:
        assert TestClient(main.app).get("/ui/config").json() == {"ui_v5": expected}
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)


def test_ui_config_needs_login():
    assert TestClient(main.app).get("/ui/config").status_code == 401
