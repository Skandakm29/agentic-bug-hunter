import sys
from pathlib import Path
# Inject the project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from backend.core.config import settings
from backend.core.ai.providers.groq import GroqProvider


class TestGroqProvider(unittest.TestCase):

    @patch("backend.core.ai.providers.groq.AsyncGroq")
    def test_provider_initialization(self, mock_async_groq):
        provider = GroqProvider()
        self.assertEqual(provider.model, settings.GROQ_MODEL)
        mock_async_groq.assert_called_once_with(api_key=settings.GROQ_API_KEY)

    @patch("backend.core.ai.providers.groq.AsyncGroq")
    def test_generate_completion_success_first_try(self, mock_async_groq):
        mock_client = MagicMock()
        mock_async_groq.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "completion output"
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        provider = GroqProvider()
        res = asyncio.run(provider.generate_completion_async(
            prompt="hello",
            system_prompt="system",
            model="custom-model",
            temperature=0.5,
            max_tokens=256
        ))

        self.assertEqual(res, "completion output")
        mock_client.chat.completions.create.assert_called_once_with(
            model="custom-model",
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"}
            ],
            temperature=0.5,
            max_tokens=256
        )

    @patch("backend.core.ai.providers.groq.AsyncGroq")
    def test_generate_completion_fallback_success(self, mock_async_groq):
        mock_client = MagicMock()
        mock_async_groq.return_value = mock_client

        # Mock the list of fallback models
        with patch.object(settings, "GROQ_FALLBACK_MODELS", ["fallback-1", "fallback-2"]):
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = "fallback success output"
            
            # First call raises an exception, second call succeeds
            mock_client.chat.completions.create = AsyncMock(side_effect=[
                Exception("API rate limit"),
                mock_response
            ])

            provider = GroqProvider()
            res = asyncio.run(provider.generate_completion_async(
                prompt="hello",
                model="primary-model"
            ))

            self.assertEqual(res, "fallback success output")
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)
            
            # Verify primary model was tried first
            mock_client.chat.completions.create.assert_any_call(
                model="primary-model",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.1,
                max_tokens=1024
            )
            # Verify fallback model was tried next
            mock_client.chat.completions.create.assert_any_call(
                model="fallback-1",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.1,
                max_tokens=1024
            )

    @patch("backend.core.ai.providers.groq.AsyncGroq")
    def test_generate_completion_all_fail(self, mock_async_groq):
        mock_client = MagicMock()
        mock_async_groq.return_value = mock_client

        with patch.object(settings, "GROQ_FALLBACK_MODELS", ["fallback-1"]):
            mock_client.chat.completions.create = AsyncMock(side_effect=[
                Exception("Primary model down"),
                Exception("Fallback model down")
            ])

            provider = GroqProvider()
            res = asyncio.run(provider.generate_completion_async(
                prompt="hello",
                model="primary-model"
            ))

            self.assertIsNone(res)
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)
