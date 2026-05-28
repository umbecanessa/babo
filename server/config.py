"""NLS Agent Runtime Server Configuration.

Settings for the multi-agent FastAPI server (local desktop or self-hosted).
Values are read from environment variables.

Environment variables:
    NLS_PRODUCT_MODE        OSS product mode ("1"/"0", default 1)
    NLS_INFERENCE_API_KEY   Optional API key for OpenAI-compatible providers
    NLS_SERVE_PORT          Server port (default: 8443)
    NLS_SERVE_HOST          Server host (default: 0.0.0.0)
    NLS_HF_MODEL            Model id sent to the OpenAI-compatible inference API
    NLS_DELEGATE_HF_MODEL   Optional model id for sub-agent / delegate loops
    NLS_GENESIS_VERSION     Default genesis template version
    NLS_DATA_DIR            Root data directory (default: ./data)
    NLS_SHARED_SECRET       Shared secret for backend-to-runtime auth
    NLS_API_KEY_PREFIX      Prefix for user-facing API keys
    NLS_SLEEP_ENABLED       Enable consolidation sleep ("1"/"0")
    RUNTIME_SHARED_SECRET   Alias for NLS_SHARED_SECRET (NestJS relay)
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class ServerSettings(BaseSettings):
    """Configuration for the Babo agent runtime FastAPI server."""

    product_mode: bool = Field(
        default=True,
        description="Open-source product mode: BYO inference, consolidation sleep only.",
    )

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8443)

    hf_model: str = Field(
        default="gpt-4o-mini",
        description="Model id sent to the OpenAI-compatible inference API.",
    )
    delegate_hf_model: str = Field(
        default="",
        description="Optional model id for delegate/sub-agent loops (empty = use hf_model).",
    )
    inference_api_key: str = Field(
        default="",
        description="Optional API key for the inference provider.",
    )

    vllm_base_url: str = Field(
        default="http://localhost:8000",
        description="OpenAI-compatible inference base URL.",
    )

    default_genesis: str = Field(default="standard-v1")

    data_dir: Path = Field(
        default_factory=lambda: _project_root() / "data",
    )

    @property
    def agents_dir(self) -> Path:
        return self.data_dir / "agents"

    @property
    def genesis_dir(self) -> Path:
        return self.data_dir / "genesis"

    sleep_enabled: bool = Field(default=True)
    agent_whitelist: str = Field(default="")

    shared_secret: str = Field(default="")
    api_key_prefix: str = Field(default="nlsk_")

    default_max_tokens: int = Field(default=512)
    default_temperature: float = Field(default=0.7)
    default_top_p: float = Field(default=0.9)

    max_agents_vram: int = Field(default=50)
    eviction_timeout_minutes: int = Field(default=30)
    eviction_hard_timeout_hours: int = Field(default=24)
    dream_tick_interval: float = Field(default=30.0)

    model_config = {
        "env_prefix": "NLS_",
        "env_file": ".env",
        "extra": "ignore",
    }


def get_settings() -> ServerSettings:
    from server.product_mode import apply_product_defaults

    settings = ServerSettings()
    if settings.product_mode:
        apply_product_defaults(settings)
    return settings
