"""Testes de app/decks/llm/images.py — mocka httpx, sem chamada real (essas são feitas à parte, manualmente)."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.decks.llm import images


def _fake_async_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestGenerateImageDispatch:
    @pytest.mark.asyncio
    async def test_defaults_to_gemini_when_provider_unset(self, monkeypatch):
        monkeypatch.delenv("DECK_IMAGE_PROVIDER", raising=False)
        with patch(
            "app.decks.llm.images._generate_image_gemini", new=AsyncMock(return_value=(b"x", "image/png"))
        ) as m_gemini:
            await images.generate_image("prompt")
        m_gemini.assert_awaited_once_with("prompt")

    @pytest.mark.asyncio
    async def test_routes_to_openai_when_configured(self, monkeypatch):
        monkeypatch.setenv("DECK_IMAGE_PROVIDER", "openai")
        with patch(
            "app.decks.llm.images._generate_image_openai",
            new=AsyncMock(return_value=(b"x", "image/png")),
        ) as m_openai:
            await images.generate_image("prompt")
        m_openai.assert_awaited_once_with("prompt")

    @pytest.mark.asyncio
    async def test_raises_for_unknown_provider(self, monkeypatch):
        monkeypatch.setenv("DECK_IMAGE_PROVIDER", "midjourney")
        with pytest.raises(images.ImageGenerationError, match="midjourney"):
            await images.generate_image("prompt")


class TestGenerateImageGemini:
    @pytest.mark.asyncio
    async def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(images.ImageGenerationError, match="GEMINI_API_KEY"):
            await images._generate_image_gemini("um gato programando")

    @pytest.mark.asyncio
    async def test_decodes_inline_image_from_response(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        raw_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
        b64 = base64.b64encode(raw_bytes).decode()

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": b64}}]}}
            ]
        }
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            image_bytes, mime_type = await images._generate_image_gemini("um gato programando")

        assert image_bytes == raw_bytes
        assert mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_raises_when_response_has_no_image(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "não consegui gerar a imagem"}]}}]
        }
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(images.ImageGenerationError, match="não retornou nenhuma imagem"):
                await images._generate_image_gemini("um gato programando")


class TestGenerateImageOpenai:
    """
    Provider ativo desde 2026-08-21, substituindo o Pollinations (ver
    docstring do módulo) — validado manualmente com chave real antes de
    entrar em produção (imagem de teste real gerada e conferida à mão).
    """

    @pytest.mark.asyncio
    async def test_raises_when_api_key_missing(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(images.ImageGenerationError, match="OPENAI_API_KEY"):
            await images._generate_image_openai("um gato programando")

    @pytest.mark.asyncio
    async def test_decodes_b64_image_from_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        raw_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
        b64 = base64.b64encode(raw_bytes).decode()

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"data": [{"b64_json": b64}], "output_format": "png"}
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            image_bytes, mime_type = await images._generate_image_openai("um gato programando")

        assert image_bytes == raw_bytes
        assert mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_sends_high_quality_by_default(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.delenv("OPENAI_IMAGE_QUALITY", raising=False)
        monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)
        raw_bytes = b"fake-bytes"
        b64 = base64.b64encode(raw_bytes).decode()

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"data": [{"b64_json": b64}], "output_format": "png"}
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            await images._generate_image_openai("um gato programando")

        sent_json = fake_client.post.call_args.kwargs["json"]
        assert sent_json["quality"] == "high"
        assert sent_json["model"] == "gpt-image-1"
        assert sent_json["prompt"] == "um gato programando"

    @pytest.mark.asyncio
    async def test_quality_is_configurable_via_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.setenv("OPENAI_IMAGE_QUALITY", "medium")
        raw_bytes = b"fake-bytes"
        b64 = base64.b64encode(raw_bytes).decode()

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"data": [{"b64_json": b64}], "output_format": "png"}
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            await images._generate_image_openai("prompt")

        assert fake_client.post.call_args.kwargs["json"]["quality"] == "medium"

    @pytest.mark.asyncio
    async def test_raises_when_response_has_no_image(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {"data": []}
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(images.ImageGenerationError, match="não retornou nenhuma imagem"):
                await images._generate_image_openai("um gato programando")
