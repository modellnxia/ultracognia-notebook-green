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
    async def test_routes_to_pollinations_when_configured(self, monkeypatch):
        monkeypatch.setenv("DECK_IMAGE_PROVIDER", "pollinations")
        with patch(
            "app.decks.llm.images._generate_image_pollinations",
            new=AsyncMock(return_value=(b"x", "image/jpeg")),
        ) as m_poll:
            await images.generate_image("prompt")
        m_poll.assert_awaited_once_with("prompt")

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


class TestGenerateImagePollinations:
    @pytest.mark.asyncio
    async def test_returns_bytes_and_content_type_on_success(self):
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.headers = {"content-type": "image/jpeg; charset=utf-8"}
        fake_response.content = b"fake-jpeg-bytes"
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            image_bytes, mime_type = await images._generate_image_pollinations("um gato programando")

        assert image_bytes == b"fake-jpeg-bytes"
        assert mime_type == "image/jpeg"
        fake_client.get.assert_awaited_once()
        called_url = fake_client.get.call_args.args[0]
        assert "um%20gato%20programando" in called_url or "um+gato+programando" in called_url

    @pytest.mark.asyncio
    async def test_raises_when_response_is_not_an_image(self):
        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.headers = {"content-type": "text/html"}
        fake_response.content = b"<html>erro</html>"
        fake_client = _fake_async_client(fake_response)

        with patch("app.decks.llm.images.httpx.AsyncClient", return_value=fake_client):
            with pytest.raises(images.ImageGenerationError, match="não devolveu uma imagem"):
                await images._generate_image_pollinations("um gato programando")
