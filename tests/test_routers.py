"""
Unit + integration tests for app/routers/report.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.models.report import ReportResponse, NotebookDefaultResponse


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
    notebook_title="Mock Report",
    report="Conteúdo do relatório mockado.",
    report_path="/tmp/mock_relatorio.md",
)

_MOCK_SLIDES_RESPONSE = NotebookDefaultResponse(
    notebook_id="nb-slides-01",
    message="Slides criados com sucesso",
    status=True,
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
        assert r.json()["service"] == "brain_notebooklm"


# ─────────────────────────────────────────────────────────────────────────────
# POST /report/generate
# ─────────────────────────────────────────────────────────────────────────────


class TestGenerateReport:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.fixture
    def payload(self):
        return {
            "messages": ["Hello from user", "Hi from assistant"],
            "notebook_title": "Teste",
        }

    @pytest.mark.asyncio
    async def test_mock_mode_returns_200(self, app, payload):
        with (
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 200
        assert r.json()["notebook_id"] == "nb-mock-01"

    @pytest.mark.asyncio
    async def test_real_mode_calls_create_report(self, app, payload):
        with (
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ) as mock_create,
        ):
            mock_settings.USE_MOCK_REPORT = False
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 200
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_service_exception_returns_500(self, app, payload):
        with (
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 500
        assert "boom" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_messages_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post("/report/generate", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_with_optional_notebook_id(self, app):
        payload = {
            "messages": ["msg"],
            "notebook_title": "T",
            "notebook_id": "existing-nb-id",
        }
        with (
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate", json=payload)
        assert r.status_code == 200


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
        """Passing a non-string (dict) for notebook_id should fail validation."""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/create-slides",
                json={"notebook_id": {"bad": "value"}},
            )
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /report/generate-from-db
# ─────────────────────────────────────────────────────────────────────────────


_USER_ID = str(uuid.uuid4())
_DATE = "2026-05-18"


def _db_payload(user_id=_USER_ID, target_date=_DATE, **kwargs):
    return {
        "user_id": user_id,
        "target_date": target_date,
        "notebook_title": "Rel DB",
        **kwargs,
    }


def _fake_rows(count=2):
    """Fake asyncpg records as plain dicts (subscriptable)."""
    return [
        {"role": "user", "content": f"mensagem {i}", "created_at": None, "conversation_title": "conv"}
        for i in range(count)
    ]


async def _fake_get_db_conn_ok():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=_fake_rows())
    yield conn


async def _fake_get_db_conn_empty():
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    yield conn


async def _fake_get_db_conn_runtime_error():
    raise RuntimeError("Pool não inicializado")
    yield  # make it a generator


async def _fake_get_db_conn_generic_error():
    raise Exception("db explodiu")
    yield


class TestGenerateFromDb:
    @pytest.fixture
    def app(self):
        return _make_app()

    # ── Happy path (mock mode) ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mock_mode_returns_200_with_rows(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_ok()),
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_real_mode_calls_create_report(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_ok()),
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ) as mock_create,
        ):
            mock_settings.USE_MOCK_REPORT = False
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 200
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_messages_formatted_with_role(self, app):
        """Verifies that rows are formatted as [ROLE] content before create_report_mock."""
        captured = {}

        async def capture_req(req):
            captured["req"] = req
            return _MOCK_REPORT_RESPONSE

        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_ok()),
            patch("app.routers.report.settings") as mock_settings,
            patch("app.routers.report.create_report_mock", new=AsyncMock(side_effect=capture_req)),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                await ac.post("/report/generate-from-db", json=_db_payload())

        messages = captured["req"].messages
        assert all(m.startswith("[USER]") for m in messages)

    # ── 404 when no rows ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_404_when_no_messages(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_empty()),
            patch("app.routers.report.settings") as mock_settings,
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 404
        assert "Nenhuma mensagem" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_404_detail_contains_user_id(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_empty()),
            patch("app.routers.report.settings") as mock_settings,
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert _USER_ID in r.json()["detail"]

    # ── 503 when pool not initialized ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_503_on_runtime_error(self, app):
        with (
            patch(
                "app.routers.report.get_db_conn",
                return_value=_fake_get_db_conn_runtime_error(),
            ),
            patch("app.routers.report.settings") as mock_settings,
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 503

    # ── 500 on generic DB error ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_500_on_generic_db_error(self, app):
        with (
            patch(
                "app.routers.report.get_db_conn",
                return_value=_fake_get_db_conn_generic_error(),
            ),
            patch("app.routers.report.settings") as mock_settings,
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 500
        assert "db explodiu" in r.json()["detail"]

    # ── 500 when report generation fails ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_500_when_report_service_fails(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_ok()),
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(side_effect=Exception("notebooklm down")),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post("/report/generate-from-db", json=_db_payload())
        assert r.status_code == 500
        assert "notebooklm down" in r.json()["detail"]

    # ── Validation ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_invalid_user_id_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/generate-from-db",
                json=_db_payload(user_id="not-a-uuid"),
            )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_date_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/report/generate-from-db",
                json=_db_payload(target_date="not-a-date"),
            )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_fields_returns_422(self, app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            r = await ac.post("/report/generate-from-db", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_with_optional_notebook_id(self, app):
        with (
            patch("app.routers.report.get_db_conn", return_value=_fake_get_db_conn_ok()),
            patch("app.routers.report.settings") as mock_settings,
            patch(
                "app.routers.report.create_report_mock",
                new=AsyncMock(return_value=_MOCK_REPORT_RESPONSE),
            ),
        ):
            mock_settings.USE_MOCK_REPORT = True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/report/generate-from-db",
                    json=_db_payload(notebook_id="reuse-nb-id"),
                )
        assert r.status_code == 200
