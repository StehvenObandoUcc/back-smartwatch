import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RateLimit(BaseModel):
    """`limit` peticiones cada `window_seconds` segundos."""

    limit: int
    window_seconds: int


class Settings(BaseSettings):
    """Configuración leída solo de variables de entorno (y `.env` en desarrollo)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_nested_delimiter="__", extra="ignore"
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: PostgresDsn
    redis_url: RedisDsn

    db_pool_size: int = 10
    db_max_overflow: int = 10
    dependency_timeout_seconds: float = 2.0

    problem_type_base: str = "https://api.example.com/problems/"

    # Origen del panel web: único origen con CORS y credenciales (cookie de refresh).
    web_origin: str = "http://localhost:5173"

    # Tokens
    jwt_secret: SecretStr
    jwt_issuer: str = "back-smartwatch"
    access_token_ttl_seconds: int = 900
    user_refresh_ttl_days: int = 30
    device_refresh_idle_days: int = 90

    # Correos de cuenta
    email_verify_ttl_hours: int = 24
    password_reset_ttl_minutes: int = 60

    # Correo y outbox (las claves solo por variables de entorno)
    email_provider: Literal["console", "resend"] = "console"
    resend_api_key: SecretStr | None = None
    email_from: str = "Recordatorios <no-reply@localhost>"
    outbox_max_attempts: int = 5
    outbox_batch_size: int = 20
    outbox_retry_base_seconds: int = 60
    outbox_retry_max_seconds: int = 3600
    # Tras entregar, el outbox borra los parámetros del mensaje (llevan enlaces con tokens).
    # Solo desarrollo: ponerlo en false para poder leer los enlaces en la tabla.
    outbox_scrub_payload: bool = True

    # Telegram (el token del bot y el secreto del webhook solo por variables de entorno)
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_link_ttl_minutes: int = 15

    # Chat con IA (la clave de DeepSeek solo por variable de entorno)
    chat_provider: Literal["deepseek", "fake"] = "fake"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    chat_daily_messages: int = 30
    chat_timeout_seconds: float = 20.0
    chat_web_max_tokens: int = 500
    chat_watch_max_tokens: int = 150

    # Avisos y reportes
    missed_alert_lookback_hours: int = 6
    report_batch_size: int = 10

    # Vinculación del reloj (RFC 8628)
    pairing_code_ttl_seconds: int = 600
    pairing_poll_interval_seconds: int = 5

    # Argon2id (valores por defecto de argon2-cffi / RFC 9106 "low memory").
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4

    # Límites de peticiones
    rate_register_ip: RateLimit = RateLimit(limit=5, window_seconds=3600)
    rate_login_ip: RateLimit = RateLimit(limit=20, window_seconds=900)
    rate_login_email: RateLimit = RateLimit(limit=5, window_seconds=900)
    rate_invitation_patient: RateLimit = RateLimit(limit=10, window_seconds=86400)
    rate_invitation_accept_user: RateLimit = RateLimit(limit=10, window_seconds=900)
    rate_invitation_accept_ip: RateLimit = RateLimit(limit=30, window_seconds=900)
    rate_pairing_create_ip: RateLimit = RateLimit(limit=10, window_seconds=3600)
    rate_pairing_confirm_user: RateLimit = RateLimit(limit=10, window_seconds=900)
    rate_pairing_confirm_ip: RateLimit = RateLimit(limit=30, window_seconds=900)
    rate_device_token_ip: RateLimit = RateLimit(limit=120, window_seconds=60)
    rate_verify_email_ip: RateLimit = RateLimit(limit=20, window_seconds=900)
    rate_resend_verification_user: RateLimit = RateLimit(limit=3, window_seconds=3600)
    rate_forgot_password_ip: RateLimit = RateLimit(limit=10, window_seconds=3600)
    rate_forgot_password_email: RateLimit = RateLimit(limit=3, window_seconds=3600)
    rate_reset_password_ip: RateLimit = RateLimit(limit=20, window_seconds=900)
    rate_telegram_link_user: RateLimit = RateLimit(limit=5, window_seconds=3600)
    rate_report_patient: RateLimit = RateLimit(limit=5, window_seconds=3600)
    rate_chat_user: RateLimit = RateLimit(limit=10, window_seconds=60)

    @model_validator(mode="after")
    def _email_provider_is_usable(self) -> "Settings":
        if self.email_provider == "resend" and self.resend_api_key is None:
            raise ValueError("EMAIL_PROVIDER=resend requiere RESEND_API_KEY")
        if self.email_provider == "console" and self.app_env in ("staging", "production"):
            raise ValueError("EMAIL_PROVIDER=console escribe enlaces en el log: solo local y test")
        if self.chat_provider == "deepseek" and self.deepseek_api_key is None:
            raise ValueError("CHAT_PROVIDER=deepseek requiere DEEPSEEK_API_KEY")
        if self.chat_provider == "fake" and self.app_env in ("staging", "production"):
            raise ValueError("CHAT_PROVIDER=fake no habla con ningún modelo: solo local y test")
        secret = self.telegram_webhook_secret
        if secret is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{16,256}", secret.get_secret_value()
        ):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET: 16-256 caracteres entre A-Z a-z 0-9 _ -")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # campos obligatorios salen del entorno
