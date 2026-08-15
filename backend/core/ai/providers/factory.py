from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.ai.providers.groq import GroqProvider


class LLMProviderFactory:
    """Factory registry returning active LLM provider configurations."""

    _providers = {
        "groq": GroqProvider
    }

    @classmethod
    def get_provider(cls, name: str = "groq") -> BaseLLMProvider:
        provider_class = cls._providers.get(name.lower())
        if not provider_class:
            raise ValueError(f"Unsupported LLM provider: {name}")
        return provider_class()
