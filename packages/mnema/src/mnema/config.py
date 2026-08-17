"""Central configuration. Every hardcoded value in the codebase lives here.

Usage:
    from mnema.config import settings
    settings.db_url          # database URL
    settings.ollama_host     # Ollama base URL
    settings.default_confidence  # observation confidence

All values are overridable via environment variables (prefix: MNEMA_).
Example:
    MNEMA_DB_URL=postgresql+psycopg2://user:pass@host/mnema
    MNEMA_OLLAMA_HOST=http://ollama-service:11434
    MNEMA_OLLAMA_MODEL=llama3.1:8b
    MNEMA_API_KEY=your-secret-key
    MNEMA_LOG_LEVEL=INFO
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MnemaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MNEMA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    db_url: str = Field(
        default="sqlite:///mnema.db",
        description="SQLAlchemy database URL. Use absolute path for SQLite in production.",
    )

    # ── LLM (Ollama) ──────────────────────────────────────────────────────────
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama REST API base URL.",
    )
    ollama_model: str = Field(
        default="qwen2.5:7b",
        description="Ollama model tag used by the consolidator.",
    )
    ollama_timeout: float = Field(
        default=60.0,
        description="Seconds before an Ollama call times out.",
        gt=0,
    )

    # ── Embedding ─────────────────────────────────────────────────────────────
    embed_model: str = Field(
        default="nomic-embed-text",
        description=(
            "Embedding model for semantic retrieval. "
            "Used by OllamaEmbedder (pull with: ollama pull nomic-embed-text). "
            "Set to a sentence-transformers model name when using SentenceTransformerEmbedder."
        ),
    )

    # ── Memory behaviour ─────────────────────────────────────────────────────
    default_confidence: float = Field(
        default=0.85,
        description="Confidence assigned to observations when caller omits it.",
        ge=0.0,
        le=1.0,
    )
    default_namespace: str = Field(
        default="default",
        description="Namespace used when caller omits namespace.",
    )
    digest_max_chars: int = Field(
        default=4000,
        description="Character budget for Digest.render() prompt injection block.",
        gt=0,
    )
    consolidation_min_beliefs: int = Field(
        default=2,
        description="Minimum active beliefs before consolidation is attempted.",
        ge=2,
    )

    # ── API security ─────────────────────────────────────────────────────────
    api_key: str = Field(
        default="",
        description=(
            "Bearer token required on mutating API routes. "
            "Empty string = open (dev mode). Set in production."
        ),
    )

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(
        default="WARNING",
        description="Python logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )


settings = MnemaSettings()


def configure_logging() -> None:
    """Apply log_level from settings to the mnema logger hierarchy."""
    import logging
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("mnema").setLevel(settings.log_level.upper())
