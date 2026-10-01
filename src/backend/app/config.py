from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    opensandbox_domain: str = "http://127.0.0.1:18080"
    opensandbox_api_key: str = ""
    sandbox_image: str = "opensandbox/code-interpreter:v1.1.0"
    sandbox_timeout_seconds: int = Field(default=1800, ge=300, le=86400)
    sandbox_idle_seconds: int = Field(default=900, ge=60, le=86400)
    sandbox_request_timeout_seconds: int = Field(default=180, ge=30, le=600)
    copilot_model: str = "gpt-6-astra"
    copilot_reasoning_effort: str = "high"
    github_token: str = ""
    mcd_mcp_token: str = ""
    max_sessions: int = Field(default=25, ge=1, le=200)
    requests_per_minute: int = Field(default=20, ge=1, le=120)

    def require_runtime_secrets(self) -> None:
        missing = [
            name
            for name, value in (
                ("OPENSANDBOX_API_KEY", self.opensandbox_api_key),
                ("GITHUB_TOKEN", self.github_token),
                ("MCD_MCP_TOKEN", self.mcd_mcp_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required runtime configuration: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
