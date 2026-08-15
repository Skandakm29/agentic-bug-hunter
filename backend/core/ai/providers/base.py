from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseLLMProvider(ABC):
    """Abstract base class establishing standard connection contracts for LLM providers."""

    @abstractmethod
    async def generate_completion_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[Any] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """
        Execute an asynchronous completion task.
        """
        pass
