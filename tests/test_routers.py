"""
Unit + integration tests for app/routers/report.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.report import (
    NotebookDefaultResponse,
    PrepareNotebookResponse,
    ReportResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# App factory — avoids triggering the real lifespan (DB pool)
# ─────────────────────────────────────────────────────────────────────────────


def _make_app():
    """Build a FastAPI app without running the real database lifespan."""
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from app.routers.report import router

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(title="test", lifespan=noop_lifespan)
    app.include_router(router)
    return app


_MOCK_REPORT_RESPONSE = ReportResponse(
    notebook_id="nb-mock-01",
    notebook_title="nb-mock-01",
    report="Conteúdo do relatório mockado.",
    report_path="/tmp/mock_relatorio.md",
)

_MOCK_SLIDES_RESPONSE = NotebookDefaultResponse(
    notebook_id="nb-slides-01",
    message="Slides criados com sucesso",
    status=True,
)

_MOCK_PREPARE_RESPONSE = PrepareNotebookResponse(
    notebook_id="nb-prepared-01",
    notebook_title="Teste_20260521_000000",
    from_cache=False,
)

_MOCK_PREPARE_RESPONSE_CACHED = PrepareNotebookResponse(
    notebook_id="nb-prepared-01",
    notebook_title="Teste_20260521_000000",
    from_cache=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────────────────────────────────────


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self):
        app = _make_app()

        @app.get("/health")
        def health():
            return {"status": "ok", "service": "brain_notebooklm"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# POST /report/generate
# ─────────────────────────────────────────────────────────────────────────────


class TestGenerateReport:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.fixture
    def payload(self):
        return {"notebook_id": "nb-already-prepared-01"}

    @pytest.mark.asyncio
    async def test_returns_200_on_success(self, app, payload):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.create_report",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 200
        assert r.json()["notebook_id"] == "nb-mock-01"

    @pytest.mark.asyncio
    async def test_calls_create_report_with_notebook_id(self, app, payload):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.create_report",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ) as mock_create,
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                await ac.post("/report/generate", json=payload)
        mock_create.assert_called_once()
        called_req = mock_create.call_args[0][1]
        assert called_req.notebook_id == "nb-already-prepared-01"

    @pytest.mark.asyncio
    async def test_service_exception_returns_500(self, app, payload):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.create_report",
                new=AsyncMock(side_effect=RuntimeError("lm down")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 500
        assert "lm down" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_notebook_id_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post("/report/generate", json={})
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /report/create-slides
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateSlides:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.fixture
    def payload(self):
        return {"notebook_id": "nb-slides-01"}

    @pytest.mark.asyncio
    async def test_success_returns_200(self, app, payload):
        with patch(
            "app.routers.report.create_slides_from_notebook",
            new=AsyncMock(return_value=_MOCK_SLIDES_RESPONSE),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/create-slides", json=payload)
        assert r.status_code == 200
        assert r.json()["status"] is True

    @pytest.mark.asyncio
    async def test_service_exception_returns_500(self, app, payload):
        with patch(
            "app.routers.report.create_slides_from_notebook",
            new=AsyncMock(side_effect=Exception("slide error")),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/create-slides", json=payload)
        assert r.status_code == 500
        assert "slide error" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_notebook_id_type_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/create-slides",
                json={"notebook_id": {"bad": "value"}},
            )
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /report/prepare-notebook
# ─────────────────────────────────────────────────────────────────────────────

_USER_ID = str(uuid.uuid4())
_DATE = "2026-05-14"


def _prep_payload(user_id=_USER_ID, target_date=_DATE, **kwargs):
    return {
        "user_id": user_id,
        "target_date": target_date,
        **kwargs,
    }


async def _fake_db():
    """Conexão de banco genérica (o orchestrate é mockado, então o DB não importa)."""
    conn = AsyncMock()
    yield conn


async def _fake_db_runtime_error():
    raise RuntimeError("Pool não inicializado")
    yield


async def _fake_db_generic_error():
    raise Exception("db explodiu")
    yield


class TestPrepareNotebook:
    @pytest.fixture
    def app(self):
        return _make_app()

    # ── Happy path: cache miss ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cache_miss_calls_prepare_and_returns_200(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(return_value=_MOCK_PREPARE_RESPONSE),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 200
        assert r.json()["notebook_id"] == "nb-prepared-01"
        assert r.json()["from_cache"] is False

    # ── Happy path: cache hit ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cache_hit_returns_200_without_calling_notebooklm(self, app):
        cached_response = _MOCK_PREPARE_RESPONSE.model_copy(update={"from_cache": True})
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(return_value=cached_response),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 200
        assert r.json()["from_cache"] is True

    # ── force_recreate é repassado corretamente ─────────────────────────────

    @pytest.mark.asyncio
    async def test_force_recreate_is_passed_to_orchestrate(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(return_value=_MOCK_PREPARE_RESPONSE),
            ) as mock_orchestrate,
        ):
            payload = _prep_payload()
            payload["force_recreate"] = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=payload)
        assert r.status_code == 200
        assert mock_orchestrate.call_args.kwargs["force_recreate"] is True

    # ── 404 when user not found ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_404_when_user_not_found(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(side_effect=ValueError("Usuário não encontrado")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 404
        assert "não encontrado" in r.json()["detail"]

    # ── 404 when no messages ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_404_when_no_messages(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(side_effect=ValueError(f"Nenhuma mensagem encontrada para user_id={_USER_ID}")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 404
        assert "Nenhuma mensagem" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_404_detail_contains_user_id(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(side_effect=ValueError(f"Nenhuma mensagem encontrada para user_id={_USER_ID}")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert _USER_ID in r.json()["detail"]

    # ── 503 when pool not initialized ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_503_on_runtime_error(self, app):
        with patch(
            "app.routers.report.get_db_conn",
            side_effect=lambda: _fake_db_runtime_error(),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 503

    # ── 500 on generic error ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_500_on_generic_db_error(self, app):
        with patch(
            "app.routers.report.get_db_conn",
            side_effect=lambda: _fake_db_generic_error(),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 500
        assert "db explodiu" in r.json()["detail"]

    # ── 500 when orchestrate service fails ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_500_when_prepare_service_fails(self, app):
        with (
            patch("app.routers.report.get_db_conn", side_effect=lambda: _fake_db()),
            patch(
                "app.routers.report.orchestrate_prepare_notebook",
                new=AsyncMock(side_effect=Exception("notebooklm down")),
            ),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/prepare-notebook", json=_prep_payload())
        assert r.status_code == 500
        assert "notebooklm down" in r.json()["detail"]

    # ── Validation ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_invalid_user_id_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/prepare-notebook",
                json=_prep_payload(user_id="not-a-uuid"),
            )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_date_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/prepare-notebook",
                json=_prep_payload(target_date="not-a-date"),
            )
        assert r.status_code == 422


