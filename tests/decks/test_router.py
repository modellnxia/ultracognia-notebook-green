"""Integration tests for app/decks/router.py — mesmo padrão de tests/test_routers.py."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.decks.router import router

_NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


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
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": None, "apply_style_guardrails": False, "theme_id": None,
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
            user_id, "editorial colado pelo usuário", None, None, False, theme_id=None
        )

    @pytest.mark.asyncio
    async def test_passes_explicit_llm_provider_choice(self, app):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": "deepseek", "apply_style_guardrails": False, "theme_id": None,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(user_id), "editorial_text": "x", "llm_provider": "deepseek"},
                )

        assert resp.status_code == 201
        assert resp.json()["llm_provider"] == "deepseek"
        mock_repo.create_job.assert_awaited_once_with(user_id, "x", None, "deepseek", False, theme_id=None)

    @pytest.mark.asyncio
    async def test_returns_422_for_unknown_llm_provider(self, app):
        with patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(uuid.uuid4()), "editorial_text": "x", "llm_provider": "chatgpt"},
                )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_422_for_openrouter_removed_from_combo(self, app):
        """OpenRouter foi removido do combo em 2026-08-21 (decisão do usuário) — não é mais aceito."""
        with patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(uuid.uuid4()), "editorial_text": "x", "llm_provider": "openrouter"},
                )

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_accepts_openai_as_llm_provider_choice(self, app):
        """OpenAI novo no combo (2026-08-21) — texto e imagem, tudo OpenAI."""
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": "openai", "apply_style_guardrails": True, "theme_id": None,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(user_id), "editorial_text": "x", "llm_provider": "openai"},
                )

        assert resp.status_code == 201
        assert resp.json()["llm_provider"] == "openai"

    @pytest.mark.asyncio
    async def test_apply_style_guardrails_defaults_to_false_in_request(self, app):
        """
        Default invertido em 2026-08-21 (4) — ver test_repository.py e
        HANDOFF_FRONTEND.md seção 3.2 pro achado real que motivou isso.
        """
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": None, "apply_style_guardrails": False, "theme_id": None,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/decks", json={"user_id": str(user_id), "editorial_text": "x"})

        assert resp.status_code == 201
        assert resp.json()["apply_style_guardrails"] is False
        mock_repo.create_job.assert_awaited_once_with(user_id, "x", None, None, False, theme_id=None)

    @pytest.mark.asyncio
    async def test_passes_apply_style_guardrails_false(self, app):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": None, "apply_style_guardrails": False, "theme_id": None,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(user_id), "editorial_text": "x", "apply_style_guardrails": False},
                )

        assert resp.status_code == 201
        assert resp.json()["apply_style_guardrails"] is False
        mock_repo.create_job.assert_awaited_once_with(user_id, "x", None, None, False, theme_id=None)

    @pytest.mark.asyncio
    async def test_returns_422_when_editorial_text_missing(self, app):
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/decks", json={"user_id": str(uuid.uuid4())})

        assert resp.status_code == 422


class TestListDeckJobs:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_returns_jobs_for_user_most_recent_first(self, app):
        user_id = uuid.uuid4()
        job_id_1, job_id_2 = uuid.uuid4(), uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.list_jobs_for_user.return_value = [
            {"id": job_id_2, "status": "completed", "cost_cents": 3, "created_at": _NOW, "editorial_preview": "Editorial mais recente…"},
            {"id": job_id_1, "status": "failed", "cost_cents": 0, "created_at": _NOW, "editorial_preview": "Editorial mais antigo…"},
        ]
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks?user_id={user_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["jobs"]) == 2
        assert body["jobs"][0]["id"] == str(job_id_2)
        assert body["jobs"][0]["editorial_preview"] == "Editorial mais recente…"
        mock_repo.list_jobs_for_user.assert_awaited_once_with(user_id, 50, 0)

    @pytest.mark.asyncio
    async def test_passes_custom_limit_and_offset(self, app):
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.list_jobs_for_user.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks?user_id={user_id}&limit=10&offset=20")

        assert resp.status_code == 200
        mock_repo.list_jobs_for_user.assert_awaited_once_with(user_id, 10, 20)

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_user_has_no_jobs(self, app):
        mock_repo = AsyncMock()
        mock_repo.list_jobs_for_user.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks?user_id={uuid.uuid4()}")

        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    @pytest.mark.asyncio
    async def test_returns_422_when_user_id_missing(self, app):
        with patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/decks")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_422_when_limit_exceeds_max(self, app):
        with patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(f"/decks?user_id={uuid.uuid4()}&limit=500")

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
        mock_repo.get_job.return_value = {
            "id": job_id, "status": "completed", "cost_cents": 5, "created_at": _NOW,
            "llm_provider": "gemini", "apply_style_guardrails": True, "theme_id": None,
        }
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


class TestCreateDeckJobWithThemeId:
    """Theme Library (2026-08-24, Fatia A) — POST /decks aceita theme_id opcional."""

    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_passes_theme_id_through_to_create_job(self, app):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        theme_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": None, "apply_style_guardrails": False, "theme_id": theme_id,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks",
                    json={"user_id": str(user_id), "editorial_text": "x", "theme_id": str(theme_id)},
                )

        assert resp.status_code == 201
        assert resp.json()["theme_id"] == str(theme_id)
        mock_repo.create_job.assert_awaited_once_with(user_id, "x", None, None, False, theme_id=theme_id)

    @pytest.mark.asyncio
    async def test_omitting_theme_id_defaults_to_none(self, app):
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_job.return_value = {
            "id": job_id, "status": "pending", "cost_cents": 0, "created_at": _NOW,
            "llm_provider": None, "apply_style_guardrails": False, "theme_id": None,
        }
        mock_repo.get_steps.return_value = []
        with (
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.DeckJobRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/decks", json={"user_id": str(user_id), "editorial_text": "x"})

        assert resp.status_code == 201
        assert resp.json()["theme_id"] is None


_VALID_PALETTE = {"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"}


class TestThemeLibraryEndpoints:
    """
    Theme Library (2026-08-24, Fatia A). Endpoints atrás de
    `require_theme_admin_key` — segunda chave, além do x-api-key global
    (aqui nem entra em jogo: o middleware `validar_acesso` não está montado
    nesse app de teste, só o router isolado — mesmo padrão do resto deste
    arquivo).
    """

    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_returns_503_when_admin_key_not_configured(self, app):
        with patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "padrao", "palette": _VALID_PALETTE, "font_stack": "Inter"},
                )

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_returns_403_when_admin_key_header_missing(self, app):
        with patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "padrao", "palette": _VALID_PALETTE, "font_stack": "Inter"},
                )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_403_when_admin_key_wrong(self, app):
        with patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "padrao", "palette": _VALID_PALETTE, "font_stack": "Inter"},
                    headers={"X-Theme-Admin-Key": "chave-errada"},
                )

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_creates_theme_with_correct_admin_key(self, app):
        theme_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.create_theme.return_value = {
            "id": theme_id, "client_id": None, "name": "padrao",
            "palette": json.dumps(_VALID_PALETTE), "font_stack": "Inter", "logo_url": None,
            "created_at": _NOW, "updated_at": _NOW,
        }
        with (
            patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"),
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.ThemeRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "padrao", "palette": _VALID_PALETTE, "font_stack": "Inter"},
                    headers={"X-Theme-Admin-Key": "segredo-real"},
                )

        assert resp.status_code == 201
        assert resp.json()["id"] == str(theme_id)
        assert resp.json()["palette"] == _VALID_PALETTE
        mock_repo.create_theme.assert_awaited_once_with("padrao", _VALID_PALETTE, "Inter", None, None)

    @pytest.mark.asyncio
    async def test_rejects_low_contrast_palette(self, app):
        """Mesma regra de Theme.background_and_primary_must_be_distinguishable — sempre ativa."""
        bad_palette = {"primary": "#0A192F", "background": "#081421", "text": "#F2F2F2"}
        with patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "padrao", "palette": bad_palette, "font_stack": "Inter"},
                    headers={"X-Theme-Admin-Key": "segredo-real"},
                )

        assert resp.status_code == 422
        assert "contraste baixo demais" in resp.text

    @pytest.mark.asyncio
    async def test_accepts_light_background_palette(self):
        """Decisão do usuário (2026-08-24): tema salvo pode ser claro, sem exigir fundo escuro."""
        light_palette = {"primary": "#0A192F", "background": "#F8FAFC", "text": "#111111"}
        app = _make_app()
        mock_repo = AsyncMock()
        mock_repo.create_theme.return_value = {
            "id": uuid.uuid4(), "client_id": None, "name": "claro",
            "palette": json.dumps(light_palette), "font_stack": "Inter", "logo_url": None,
            "created_at": _NOW, "updated_at": _NOW,
        }
        with (
            patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"),
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.ThemeRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/decks/themes",
                    json={"name": "claro", "palette": light_palette, "font_stack": "Inter"},
                    headers={"X-Theme-Admin-Key": "segredo-real"},
                )

        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_lists_themes_with_correct_admin_key(self, app):
        theme_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.list_themes.return_value = [
            {
                "id": theme_id, "client_id": None, "name": "padrao",
                "palette": json.dumps(_VALID_PALETTE), "font_stack": "Inter", "logo_url": None,
                "created_at": _NOW, "updated_at": _NOW,
            }
        ]
        with (
            patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"),
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.ThemeRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/decks/themes", headers={"X-Theme-Admin-Key": "segredo-real"})

        assert resp.status_code == 200
        assert len(resp.json()["themes"]) == 1
        assert resp.json()["themes"][0]["id"] == str(theme_id)

    @pytest.mark.asyncio
    async def test_list_themes_filters_by_client_id(self, app):
        client_id = uuid.uuid4()
        mock_repo = AsyncMock()
        mock_repo.list_themes.return_value = []
        with (
            patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"),
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.ThemeRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(
                    f"/decks/themes?client_id={client_id}", headers={"X-Theme-Admin-Key": "segredo-real"}
                )

        assert resp.status_code == 200
        mock_repo.list_themes.assert_awaited_once_with(client_id)

    @pytest.mark.asyncio
    async def test_list_themes_without_admin_key_returns_403(self, app):
        with patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/decks/themes")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_get_decks_themes_does_not_collide_with_job_id_route(self, app):
        """
        `/decks/themes` precisa casar com a rota literal, não com `/{job_id}`
        (`themes` não é um UUID válido — sem a ordem certa das rotas, isso
        daria 422 de validação de UUID em vez de bater no endpoint certo).
        """
        mock_repo = AsyncMock()
        mock_repo.list_themes.return_value = []
        with (
            patch("app.decks.router.settings.DECKS_THEME_ADMIN_KEY", "segredo-real"),
            patch("app.decks.router.get_db_conn", side_effect=lambda: _fake_db_conn()),
            patch("app.decks.router.ThemeRepository", return_value=mock_repo),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/decks/themes", headers={"X-Theme-Admin-Key": "segredo-real"})

        assert resp.status_code != 422
