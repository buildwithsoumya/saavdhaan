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
    groq_api_key: str = ""
    # public demo: no token needed, family voices are per visitor, usage is capped per day.
    # Opening the app with ?t=<APP_TOKEN> skips the caps (owner access).
    public_demo: bool = False
    demo_minutes_per_day: int = 300  # live listening, all visitors together
    demo_calls_per_visitor: int = 1
    demo_calls_total: int = 3
    demo_call_max_minutes: int = 15
    demo_voices_per_visitor: int = 3  # per day
    demo_voices_total: int = 20  # per day
    demo_honeypot_turns_per_day: int = 200
    demo_reports_per_visitor: int = 5  # per day
    # read-only GitHub token for /saavdhaan.apk while the repo is private (Contents: read)
    github_token: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    honeypot_persona: str = "elderly woman"  # who the honeypot plays; match the voice, e.g. "elderly man"
    honeypot_voice: str = "Vasudha"  # neutral Timbre v3 persona, used unless the elder chose their own consented voice


settings = Settings()
