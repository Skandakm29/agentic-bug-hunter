from backend.core.ai.providers.base import BaseLLMProvider


class BaseAgent:
    """Base class for specialized agents, supporting model provider injection."""

    def __init__(self, provider: BaseLLMProvider):
        self.provider = provider
