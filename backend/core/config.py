import os


def _env_list(name: str, default: list) -> list:
    """Read a comma-separated environment variable into a list of strings."""
    raw = os.environ.get(name, "")
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    return parsed or default


class Settings:
    # API key and URL configurations
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    FRONTEND_URL: str = os.environ.get("FRONTEND_URL", "http://localhost:3000")

    # ------------------------------------------------------------------
    # Model configuration
    # ------------------------------------------------------------------
    # Groq retires hosted models on a rolling basis, and a decommissioned ID
    # fails every request in the fallback chain, which silently disables every
    # LLM feature in the platform. Every ID is therefore overridable via the
    # environment so a retirement can be worked around without a code change:
    #
    #   GROQ_MODEL="llama-3.1-8b-instant"
    #   GROQ_FALLBACK_MODELS="llama-3.3-70b-versatile,llama-3.1-8b-instant"
    #   GROQ_REASONING_MODEL="llama-3.3-70b-versatile"
    #
    # Check https://console.groq.com/docs/models for the current roster.

    # Fast, cheap model for classification and static-finding validation.
    GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")

    # Larger model for patch generation, refinement, and failure reasoning.
    GROQ_REASONING_MODEL: str = os.environ.get(
        "GROQ_REASONING_MODEL", "llama-3.3-70b-versatile"
    )

    # Tried in order when the primary model fails.
    GROQ_FALLBACK_MODELS: list = _env_list(
        "GROQ_FALLBACK_MODELS",
        [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ],
    )

    # Scoring calculation weights
    LLM_CONFIDENCE_WEIGHT: float = 0.6
    STATIC_CONFIDENCE_WEIGHT: float = 0.4


settings = Settings()
