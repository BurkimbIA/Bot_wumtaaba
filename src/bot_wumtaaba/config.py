from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    whatsapp_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    webhook_verify_token: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    burkimbia_api_url: str = "https://bia-backend.fly.dev/api/v1"
    burkimbia_api_key: SecretStr = SecretStr("")
    burkimbia_translation_model: str = "bia-translation-v1-fast"
    burkimbia_timeout_seconds: int = Field(default=330, ge=5, le=360)
    mac_bridge_api_key: SecretStr = SecretStr("")
    burkimbia_yaaba_enabled: bool = False
    graph_version: str = "v25.0"
    transcription_model: str = "gemini-3.8-flash"
    extraction_model: str = "gemini-3.8-flash"
    database_path: Path = Path("data/bot.sqlite3")
    allowed_senders: str = ""
    max_audio_bytes: int = Field(default=14_000_000, ge=1024, le=14_000_000)
    max_messages_per_hour: int = Field(default=30, ge=1)
    retention_days: int = Field(default=0, ge=0)
    media_directory: Path = Path("data/media")
    reminder_enabled: bool = True
    reminder_template_name: str = ""
    reminder_template_language: str = "fr"
    admin_password: SecretStr = SecretStr("")
    admin_cookie_secure: bool = True
    support_contact: str = "L’équipe Wuumtaaba"

    @property
    def senders(self) -> set[str]:
        return {s.strip().lstrip("+") for s in self.allowed_senders.split(",") if s.strip()}

    def validate_live(self) -> None:
        missing = [key for key in (
            "whatsapp_token", "meta_app_secret", "webhook_verify_token", "gemini_api_key"
        ) if not getattr(self, key).get_secret_value()]
        if not self.whatsapp_phone_number_id.isdigit():
            missing.append("whatsapp_phone_number_id")
        if not self.senders or not all(s.isdigit() for s in self.senders):
            missing.append("allowed_senders")
        if len(self.webhook_verify_token.get_secret_value()) < 24:
            missing.append("webhook_verify_token (24 caractères minimum)")
        if missing:
            raise ValueError("Configuration manquante ou invalide : " + ", ".join(missing))
