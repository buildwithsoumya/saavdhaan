from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent  # paths work whatever the current directory is


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gnani_api_key: str = ""
    alert_voice: str = "Shivani"
    data_dir: Path = ROOT / "data"
    # when set, API, WebSocket and reports need ?token=<value>; set it before exposing the server to a phone
    app_token: str = ""


settings = Settings()
