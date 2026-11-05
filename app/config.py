from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gnani_api_key: str = ""
    alert_voice: str = "Shivani"
    data_dir: Path = Path("data")


settings = Settings()
