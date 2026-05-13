from pydantic import AliasChoices, Field, field_validator
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

    # Multi-environment profiles (used by /api/environment)
    environment_name: str = Field(
        default="local",
        validation_alias=AliasChoices("ENVIRONMENT_NAME", "ENVIRONMENT"),
    )  # local | staging | production
    local_prometheus_url: str = "http://localhost:9090"
    local_loki_url: str = "http://localhost:3100"
    local_jaeger_url: str = "http://localhost:16686"
    staging_prometheus_url: str | None = None
    staging_loki_url: str | None = None
    staging_jaeger_url: str | None = None
    production_prometheus_url: str | None = None
    production_loki_url: str | None = None
    production_jaeger_url: str | None = None

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
    rca_mode: str = "simple"  # simple | hermes

    # RCA — Hermes agent API server
    hermes_api_url: str = "http://localhost:8642/v1"
    hermes_api_key: str | None = None
    hermes_model: str = "hermes-agent"
    hermes_timeout_seconds: float = 90.0
    hermes_tools_enabled: bool = True
    hermes_investigation_mode: str = "tools_first"  # dossier | tools_first
    hermes_max_tool_rounds: int = 4
    hermes_max_tool_calls: int = 8
    hermes_tool_lookback_max_minutes: int = 120

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

    # Watchdog mode
    watchdog_enabled: bool = False
    watchdog_interval_seconds: int = 60
    watchdog_lookback_minutes: int = 15
    watchdog_anomaly_threshold: float = 0.7

    # Notifications
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_starttls: bool = True
    alert_email: str | None = None

    @field_validator("prometheus_url", "loki_url", "jaeger_url", "hermes_api_url", mode="before")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("rca_mode", mode="before")
    @classmethod
    def validate_rca_mode(cls, v: str) -> str:
        mode = (v or "simple").lower()
        if mode not in {"simple", "hermes"}:
            raise ValueError("RCA_MODE must be either 'simple' or 'hermes'")
        return mode

    @field_validator("hermes_investigation_mode", mode="before")
    @classmethod
    def validate_hermes_investigation_mode(cls, v: str) -> str:
        mode = (v or "tools_first").lower()
        if mode not in {"dossier", "tools_first"}:
            raise ValueError("HERMES_INVESTIGATION_MODE must be either 'dossier' or 'tools_first'")
        return mode


# Module-level singleton — import this everywhere
settings = Settings()
