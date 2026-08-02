from __future__ import annotations

from backend.app.config import Settings, get_settings
from backend.app.models import Domain, Owner, Session as LoginSession
from backend.app.schemas import ThemeConfig


THEME = {
    "id": "builtin-test",
    "label": "Quantum physics · Cross-field",
    "accent": "#16a6a1",
    "secondary": "#8568df",
    "nav": "#091329",
    "paper": "#f5f8fc",
    "surface": "#ffffff",
    "ink": "#182237",
    "density": "compact",
    "typography": "technical",
    "motif": "orbit",
    "source": "builtin",
    "identity": {
        "name": "Quantum Physics Digest",
        "source": "builtin",
        "logo_kind": "generated",
        "primary_template": "quantum-physics",
    },
}


def test_first_owner_is_routed_through_domain_onboarding(authenticated_client):
    client, factory, headers = authenticated_client

    status = client.get("/api/v1/auth/status").json()
    assert status["onboarding_required"] is True
    assert status["theme"] is None

    profile = client.get("/api/v1/onboarding")
    assert profile.status_code == 200
    assert profile.json()["completed"] is False

    denied = client.put(
        "/api/v1/onboarding",
        json={
            "selected_domains": ["量子物理", "人工智能", "自定义交叉领域"],
            "primary_domain": "量子物理",
            "theme": THEME,
        },
    )
    assert denied.status_code == 403

    completed = client.put(
        "/api/v1/onboarding",
        headers=headers,
        json={
            "selected_domains": ["量子物理", "人工智能", "自定义交叉领域"],
            "primary_domain": "量子物理",
            "theme": THEME,
            "ai_personalized": False,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["completed"] is True
    assert completed.json()["selected_domains"] == ["量子物理", "人工智能", "自定义交叉领域"]

    with factory() as db:
        owner = db.get(Owner, 1)
        assert owner is not None
        assert owner.onboarding_completed is True
        assert owner.primary_domain == "量子物理"
        assert owner.theme["motif"] == "orbit"
        assert {item.name for item in db.query(Domain).all()} == {
            "量子物理",
            "人工智能",
            "自定义交叉领域",
        }

    next_status = client.get("/api/v1/auth/status").json()
    assert next_status["onboarding_required"] is False
    assert next_status["theme"]["id"] == "builtin-test"
    assert next_status["theme"]["identity"]["name"] == "Quantum Physics Digest"


def test_uploaded_logo_rejects_active_svg_content(authenticated_client):
    client, _factory, headers = authenticated_client
    invalid_theme = {
        **THEME,
        "identity": {
            "name": "Unsafe logo",
            "source": "custom",
            "logo_kind": "upload",
            "logo_data_url": "data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+PC9zdmc+",
        },
    }
    response = client.put(
        "/api/v1/onboarding",
        headers=headers,
        json={
            "selected_domains": ["Custom field"],
            "primary_domain": "Custom field",
            "theme": invalid_theme,
        },
    )
    assert response.status_code == 422


def test_ai_theme_uses_one_time_key_without_persisting(authenticated_client, monkeypatch):
    client, factory, headers = authenticated_client

    async def fake_generate(_body):
        return ThemeConfig.model_validate({**THEME, "id": "ai-test", "source": "ai"})

    monkeypatch.setattr("backend.app.api.generate_ai_theme", fake_generate)
    response = client.post(
        "/api/v1/onboarding/ai-theme",
        headers=headers,
        json={
            "selected_domains": ["EDA / TCAD", "人工智能"],
            "primary_domain": "EDA / TCAD",
            "base_url": "https://models.example.test/v1",
            "api_key": "secret-one-time-key",
            "model": "example-model",
            "style_prompt": "Precise and compact",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["theme"]["source"] == "ai"

    with factory() as db:
        owner = db.get(Owner, 1)
        assert owner is not None
        assert "secret-one-time-key" not in str(owner.__dict__)


def test_debug_mode_can_delete_owner_and_return_to_first_run(authenticated_client):
    client, factory, headers = authenticated_client

    hidden = client.delete("/api/v1/debug/owner", headers=headers)
    assert hidden.status_code == 404

    debug_settings = Settings(debug=True, scheduler_enabled=False)
    client.app.dependency_overrides[get_settings] = lambda: debug_settings
    settings = client.get("/api/v1/settings")
    assert settings.status_code == 200
    assert settings.json()["debug"] is True

    reset = client.delete("/api/v1/debug/owner", headers=headers)
    assert reset.status_code == 204, reset.text

    with factory() as db:
        assert db.get(Owner, 1) is None
        assert db.query(LoginSession).count() == 0

    status = client.get("/api/v1/auth/status")
    assert status.status_code == 200
    assert status.json()["setup_required"] is True
    assert status.json()["authenticated"] is False
