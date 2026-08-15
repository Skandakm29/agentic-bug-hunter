import logging
from typing import Any, Optional
from groq import AsyncGroq
from backend.core.config import settings
from backend.core.ai.providers.base import BaseLLMProvider

logger = logging.getLogger("backend.ai.providers.groq")


class GroqProvider(BaseLLMProvider):
    """Concrete implementation of BaseLLMProvider wrapping Groq Cloud APIs."""

    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        self.model = settings.GROQ_MODEL

    async def generate_completion_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[Any] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Build list of models to try (primary model followed by fallback models)
        primary_model = model or self.model
        fallback_models = getattr(settings, "GROQ_FALLBACK_MODELS", [])
        
        models_to_try = [primary_model]
        for m in fallback_models:
            if m not in models_to_try:
                models_to_try.append(m)

        extra_params = {}
        if response_format and response_format.get("type") == "json_object":
            extra_params["response_format"] = {"type": "json_object"}

        temp = temperature if temperature is not None else 0.1
        tokens = max_tokens if max_tokens is not None else 1024

        last_exception = None
        for current_model in models_to_try:
            try:
                logger.info(f"Attempting completion with model: {current_model}")
                response = await self.client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    temperature=temp,
                    max_tokens=tokens,
                    **extra_params
                )
                return response.choices[0].message.content
            except Exception as e:
                last_exception = e
                logger.warning(
                    f"[GroqProvider] API Error with model {current_model}: {e}. "
                    f"Trying next fallback model if available."
                )

        logger.error(f"[GroqProvider] All models failed. Last error: {last_exception}")
        return None
