from pydantic import HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Observability backends
    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"
    jaeger_url: str = "http://localhost:16686"

    # HTTP client
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3

    # Aggregator behaviour
    default_lookback_minutes: int = 30
    max_log_lines: int = 500
    max_traces: int = 50

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    log_level: str = "info"

    # RCA — Anthropic
    anthropic_api_key: str | None = None
    rca_enabled: bool = True

    # RCA — GitHub
    github_token: str | None = None
    github_repo: str | None = None          # "owner/repo"
    github_default_branch: str = "main"

    @field_validator("prometheus_url", "loki_url", "jaeger_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


# Module-level singleton — import this everywhere
settings = Settings()
