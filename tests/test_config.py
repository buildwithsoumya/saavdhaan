from app.config import Settings


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setenv("ALERT_VOICE", "Avinash")
    s = Settings(_env_file=None)
    assert s.gnani_api_key == "test-key"
    assert s.alert_voice == "Avinash"
