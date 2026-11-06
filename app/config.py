from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gnani_api_key: str = ""
    alert_voice: str = "Shivani"
    data_dir: Path = Path("data")
    # when set, API, WebSocket and reports need ?token=<value>; set it before exposing the server to a phone
    app_token: str = ""


settings = Settings()
