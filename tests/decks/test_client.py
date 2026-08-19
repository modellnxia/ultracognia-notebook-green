"""
Testes de app/decks/llm/client.py — não existia nenhum teste pra este módulo
até agora. Foco principal: o suporte a `reference_images` (multimodal,
2026-08-19 — few-shot visual pro `structure`), mas cobre o dispatch básico
de fallback entre providers também, já que estava sem cobertura nenhuma.
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.decks.llm import client


def _fake_response(text: str, prompt_tokens: int = 10, output_tokens: int = 20) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": prompt_tokens, "candidatesTokenCount": output_tokens},
    }
    return resp


def _fake_async_client(response) -> AsyncMock:
    c = AsyncMock()
    c.post = AsyncMock(return_value=response)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


class TestCallGeminiPayload:
    @pytest.mark.asyncio
    async def test_text_only_when_no_reference_images(self):
        fake_client = _fake_async_client(_fake_response("resposta"))
        with patch("app.decks.llm.client.httpx.AsyncClient", return_value=fake_client):
            await client._call_gemini("http://fake", "key", "meu prompt")

        payload = fake_client.post.call_args.kwargs["json"]
        assert payload["contents"][0]["parts"] == [{"text": "meu prompt"}]

    @pytest.mark.asyncio
    async def test_images_go_before_text_part(self):
        fake_client = _fake_async_client(_fake_response("resposta"))
        images = [(b"fake-png-bytes", "image/png"), (b"fake-jpg-bytes", "image/jpeg")]

        with patch("app.decks.llm.client.httpx.AsyncClient", return_value=fake_client):
            await client._call_gemini("http://fake", "key", "meu prompt", images)

        parts = fake_client.post.call_args.kwargs["json"]["contents"][0]["parts"]
        assert len(parts) == 3
        assert parts[0]["inline_data"]["mime_type"] == "image/png"
        assert parts[0]["inline_data"]["data"] == base64.b64encode(b"fake-png-bytes").decode()
        assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
        assert parts[2] == {"text": "meu prompt"}

    @pytest.mark.asyncio
    async def test_returns_text_and_token_usage(self):
        fake_client = _fake_async_client(_fake_response("resposta do modelo", 100, 200))
        with patch("app.decks.llm.client.httpx.AsyncClient", return_value=fake_client):
            text, in_tok, out_tok = await client._call_gemini("http://fake", "key", "prompt")

        assert (text, in_tok, out_tok) == ("resposta do modelo", 100, 200)


class TestGenerateTextDispatch:
    @pytest.mark.asyncio
    async def test_skips_provider_without_api_key_configured(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        conn = object()
        providers = [{"name": "gemini", "url": "http://fake", "priority": 1}]

        with patch("app.decks.llm.client.list_active_providers", new=AsyncMock(return_value=providers)):
            with pytest.raises(client.LLMError, match="Nenhum provider"):
                await client.generate_text("prompt", conn)

    @pytest.mark.asyncio
    async def test_calls_gemini_with_reference_images_when_gemini_is_first(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        conn = object()
        providers = [{"name": "gemini", "url": "http://fake", "priority": 1}]
        images = [(b"x", "image/png")]

        with (
            patch("app.decks.llm.client.list_active_providers", new=AsyncMock(return_value=providers)),
            patch(
                "app.decks.llm.client._call_gemini",
                new=AsyncMock(return_value=("ok", 1, 2)),
            ) as m_gemini,
        ):
            result = await client.generate_text("prompt", conn, reference_images=images)

        assert result == ("ok", 1, 2)
        m_gemini.assert_awaited_once_with("http://fake", "fake-key", "prompt", images)

    @pytest.mark.asyncio
    async def test_falls_back_to_next_provider_when_first_fails(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")
        conn = object()
        providers = [
            {"name": "gemini", "url": "http://gemini", "priority": 1},
            {"name": "deepseek", "url": "http://deepseek", "priority": 2},
        ]

        with (
            patch("app.decks.llm.client.list_active_providers", new=AsyncMock(return_value=providers)),
            patch(
                "app.decks.llm.client._call_gemini",
                new=AsyncMock(side_effect=RuntimeError("Gemini fora do ar")),
            ),
            patch(
                "app.decks.llm.client._call_openai_compatible",
                new=AsyncMock(return_value=("ok do deepseek", 5, 6)),
            ) as m_deepseek,
        ):
            result = await client.generate_text("prompt", conn)

        assert result == ("ok do deepseek", 5, 6)
        m_deepseek.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warns_and_ignores_images_for_non_gemini_provider(self, monkeypatch, caplog):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        conn = object()
        providers = [{"name": "deepseek", "url": "http://deepseek", "priority": 1}]

        with (
            patch("app.decks.llm.client.list_active_providers", new=AsyncMock(return_value=providers)),
            patch(
                "app.decks.llm.client._call_openai_compatible",
                new=AsyncMock(return_value=("ok", 1, 1)),
            ) as m_call,
        ):
            await client.generate_text("prompt", conn, reference_images=[(b"x", "image/png")])

        # não quebra — só ignora a imagem e segue só com texto
        m_call.assert_awaited_once()
        assert "não suporta imagem" in caplog.text

    @pytest.mark.asyncio
    async def test_raises_when_all_providers_fail(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        conn = object()
        providers = [{"name": "gemini", "url": "http://fake", "priority": 1}]

        with (
            patch("app.decks.llm.client.list_active_providers", new=AsyncMock(return_value=providers)),
            patch(
                "app.decks.llm.client._call_gemini",
                new=AsyncMock(side_effect=RuntimeError("500")),
            ),
        ):
            with pytest.raises(client.LLMError, match="Todos os providers"):
                await client.generate_text("prompt", conn)
