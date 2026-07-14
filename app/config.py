from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for env-driven configuration.

    No call site outside this module should read `os.environ` directly.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Persistence
    database_path: str = "./data/agent_service.db"

    # Runner
    sim_speed: float = 1.0

    # Telemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "agent-service"
    otel_service_version: str = "0.1.0"

    # API
    idempotency_key_ttl_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()
