import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Tests never see the developer's .env: no real keys, no token, temp data dir."""
    monkeypatch.setattr(settings, "gnani_api_key", "test-key")
    monkeypatch.setattr(settings, "app_token", "")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
