"""Integration tests for app/decks/router.py — mesmo padrão de tests/test_routers.py."""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.decks.router import router


def _make_app() -> FastAPI:
    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(title="test", lifespan=noop_lifespan)
    app.include_router(router)
    return app


async def _fake_db_conn():
    yield object()


class TestCreateDeckJob:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_returns_201_with_pending_steps(self, app):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0,
        }
        mock_repo.get_steps.return_value = [
            {"step": "structure", "status": "pending", "attempt": 0, "output_ref": None, "error": None}
        ]
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(user_id), "editorial_text": "editorial colado pelo usuário"},
                )

        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == str(job_id)
        assert body["status"] == "pending"
        assert body["steps"][0]["step"] == "structure"
        mock_repo.create_job.assert_awaited_once_with(
            user_id, "editorial colado pelo usuário", None
        )

    @pytest.mark.asyncio
    async def test_returns_422_when_editorial_text_missing(self, app):
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/decks", json={"user_id": str(uuid.uuid4())})

        assert resp.status_code == 422


class TestGetDeckJobStatus:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_returns_404_when_job_missing(self, app):
        mock_repo = AsyncMock()
        mock_repo.get_job.return_value = None
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{uuid.uuid4()}")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_status_when_job_exists(self, app):
        job_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.get_job.return_value = {"id": job_id, "status": "completed", "cost_cents": 5}
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{job_id}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


class TestDownloadDeckPdf:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_returns_404_when_render_step_not_completed(self, app):
        mock_repo = AsyncMock()
        mock_repo.get_steps.return_value = [
            {"step": "render", "status": "pending", "output_ref": None},
        ]
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{uuid.uuid4()}/download", follow_redirects=False)

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_defaults_to_pdf_and_redirects_to_freshly_signed_url(self, app):
        mock_repo = AsyncMock()
        mock_repo.get_steps.return_value = [
            {
                "step": "render", "status": "completed",
                "output_ref": '{"pdf": "job123/deck.pdf", "pptx": "job123/deck.pptx"}',
            },
        ]
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
            patch(
                "app.decks.router.create_signed_url",
                new=AsyncMock(return_value="https://proj.supabase.co/storage/v1/object/sign/decks/job123/deck.pdf?token=abc"),
            ) as m_sign,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{uuid.uuid4()}/download", follow_redirects=False)

        assert resp.status_code in (302, 307)
        assert resp.headers["location"].endswith("token=abc")
        m_sign.assert_awaited_once_with("job123/deck.pdf")

    @pytest.mark.asyncio
    async def test_format_pptx_signs_the_pptx_object(self, app):
        mock_repo = AsyncMock()
        mock_repo.get_steps.return_value = [
            {
                "step": "render", "status": "completed",
                "output_ref": '{"pdf": "job123/deck.pdf", "pptx": "job123/deck.pptx"}',
            },
        ]
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
            patch(
                "app.decks.router.create_signed_url",
                new=AsyncMock(return_value="https://proj.supabase.co/storage/v1/object/sign/decks/job123/deck.pptx?token=xyz"),
            ) as m_sign,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{uuid.uuid4()}/download?format=pptx", follow_redirects=False)

        assert resp.status_code in (302, 307)
        m_sign.assert_awaited_once_with("job123/deck.pptx")

    @pytest.mark.asyncio
    async def test_invalid_format_returns_422(self, app):
        mock_repo = AsyncMock()
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks/{uuid.uuid4()}/download?format=keynote", follow_redirects=False)

        assert resp.status_code == 422
