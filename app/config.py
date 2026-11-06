from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gnani_api_key: str = ""
    alert_voice: str = "Shivani"
    data_dir: Path = Path("data")
    # when set, API, WebSocket and reports need ?token=<value>; set it before exposing the server to a phone
    app_token: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    honeypot_voice: str = "Vasudha"  # neutral Timbre v3 persona, never a clone of the elder


settings = Settings()
