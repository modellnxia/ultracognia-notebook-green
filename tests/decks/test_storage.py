"""Testes do cliente do Supabase Storage (app/decks/storage.py) — httpx mockado."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.decks import storage


def _mock_client(response: httpx.Response) -> MagicMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.fixture(autouse=True)
def env_creds(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")


class TestMissingCredentials:
    def test_raises_when_url_missing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            storage._base_url()

    def test_raises_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
            storage._service_role_key()


class TestEnsureBucketExists:
    @pytest.mark.asyncio
    async def test_ok_on_success(self):
        response = httpx.Response(200, request=httpx.Request("POST", "http://x"))
        ctx, client = _mock_client(response)
        with patch("app.decks.storage.httpx.AsyncClient", return_value=ctx):
            await storage.ensure_bucket_exists()
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_treats_already_exists_as_success(self):
        response = httpx.Response(
            400, json={"message": "Bucket already exists"}, request=httpx.Request("POST", "http://x")
        )
        ctx, _ = _mock_client(response)
        with patch("app.decks.storage.httpx.AsyncClient", return_value=ctx):
            await storage.ensure_bucket_exists()  # não deve levantar

    @pytest.mark.asyncio
    async def test_raises_on_real_error(self):
        response = httpx.Response(
            403, json={"message": "unauthorized"}, request=httpx.Request("POST", "http://x")
        )
        ctx, _ = _mock_client(response)
        with patch("app.decks.storage.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(httpx.HTTPStatusError):
                await storage.ensure_bucket_exists()


class TestUploadObject:
    @pytest.mark.asyncio
    async def test_posts_content_to_correct_path(self):
        response = httpx.Response(200, request=httpx.Request("POST", "http://x"))
        ctx, client = _mock_client(response)
        with patch("app.decks.storage.httpx.AsyncClient", return_value=ctx):
            await storage.upload_object("job1/deck.pdf", b"%PDF-fake", "application/pdf")

        client.post.assert_awaited_once()
        args, kwargs = client.post.call_args
        assert args[0] == "/storage/v1/object/decks/job1/deck.pdf"
        assert kwargs["content"] == b"%PDF-fake"
        assert kwargs["headers"]["Content-Type"] == "application/pdf"


class TestCreateSignedUrl:
    @pytest.mark.asyncio
    async def test_builds_full_url_from_signed_path(self):
        response = httpx.Response(
            200,
            json={"signedURL": "/object/sign/decks/job1/deck.pdf?token=abc"},
            request=httpx.Request("POST", "http://x"),
        )
        ctx, client = _mock_client(response)
        with patch("app.decks.storage.httpx.AsyncClient", return_value=ctx):
            url = await storage.create_signed_url("job1/deck.pdf", expires_in=120)

        assert url == "https://proj.supabase.co/storage/v1/object/sign/decks/job1/deck.pdf?token=abc"
        _, kwargs = client.post.call_args
        assert kwargs["json"] == {"expiresIn": 120}
