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
    max_traces: int = 200

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
    github_path_prefix: str | None = None  # if unset, derived per-query as "demo/{target}"

    # Infrastructure config file paths (relative to project root / WORKDIR /app)
    prometheus_config_path: str = "infra/prometheus.yml"
    service_registry_path: str = "infra/service-registry.yml"

    # Query/incident history (SQLite) — persisted via a Docker volume at /app/data
    history_db_path: str = "data/history.db"

    # Demo runner — URLs for the in-browser scenario execution feature
    # These must be reachable from inside the aggregator container.
    demo_service_b_url: str = "http://service-b:8002"
    demo_service_a_url: str = "http://service-a:8001"
    demo_service_c_url: str = "http://service-c:8003"
    demo_service_d_url: str = "http://service-d:8004"

    @field_validator("prometheus_url", "loki_url", "jaeger_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


# Module-level singleton — import this everywhere
settings = Settings()