from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    whatsapp_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    webhook_verify_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    graph_version: str = "v25.0"
    transcription_model: str = "gpt-4o-mini-transcribe"
    extraction_model: str = "gpt-4o-mini"
    database_path: Path = Path("data/bot.sqlite3")
    allowed_senders: str = ""
    max_audio_bytes: int = Field(default=16_000_000, ge=1024, le=24_000_000)
    max_messages_per_hour: int = Field(default=30, ge=1)
    retention_days: int = Field(default=30, ge=1)
    support_contact: str = "L’équipe Wuumtaaba"

    @property
    def senders(self) -> set[str]:
        return {s.strip().lstrip("+") for s in self.allowed_senders.split(",") if s.strip()}

    def validate_live(self) -> None:
        missing = [key for key in (
            "whatsapp_token", "meta_app_secret", "webhook_verify_token", "openai_api_key"
        ) if not getattr(self, key).get_secret_value()]
        if not self.whatsapp_phone_number_id.isdigit():
            missing.append("whatsapp_phone_number_id")
        if not self.senders or not all(s.isdigit() for s in self.senders):
            missing.append("allowed_senders")
        if len(self.webhook_verify_token.get_secret_value()) < 24:
            missing.append("webhook_verify_token (24 caractères minimum)")
        if missing:
            raise ValueError("Configuration manquante ou invalide : " + ", ".join(missing))
