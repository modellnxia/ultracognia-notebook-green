"""Testes de app/routers/notebook_chat.py — mesmo padrão de test_routers.py (app isolado, sem lifespan real)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.notebook_chat import (
    NotebookChatResponse,
    NotebookStudioItemsResponse,
    NoteInfo,
    ReportArtifactInfo,
)
from app.services.notebook_chat_service import (
    AmbiguousNotebookTitleError,
    ArtifactContentUnavailableError,
    NotebookNotFoundError,
)


def _make_app():
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    from app.routers.notebook_chat import router

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(title="test", lifespan=noop_lifespan)
    app.include_router(router)
    return app


_PAYLOAD = {"notebook_title": "Presidencia Funchal", "question": "Do que se trata?"}
_MOCK_RESPONSE = NotebookChatResponse(answer="A presidência trata de X e Y.")


class TestAskNotebookEndpoint:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_success_returns_200_with_answer(self, app):
        with patch(
            "app.routers.notebook_chat.ask_notebook",
            new=AsyncMock(return_value=_MOCK_RESPONSE),
        ) as m_ask:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post("/notebook-chat/ask", json=_PAYLOAD)

        assert r.status_code == 200
        assert r.json() == {"answer": "A presidência trata de X e Y."}
        m_ask.assert_awaited_once_with("Presidencia Funchal", "Do que se trata?")

    @pytest.mark.asyncio
    async def test_notebook_not_found_returns_404(self, app):
        with patch(
            "app.routers.notebook_chat.ask_notebook",
            new=AsyncMock(side_effect=NotebookNotFoundError("Nenhum notebook encontrado com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post("/notebook-chat/ask", json=_PAYLOAD)

        assert r.status_code == 404
        assert "Nenhum notebook encontrado" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_ambiguous_title_returns_409(self, app):
        with patch(
            "app.routers.notebook_chat.ask_notebook",
            new=AsyncMock(side_effect=AmbiguousNotebookTitleError("Encontrados 2 notebooks com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post("/notebook-chat/ask", json=_PAYLOAD)

        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self, app):
        with patch(
            "app.routers.notebook_chat.ask_notebook",
            new=AsyncMock(side_effect=Exception("falha de rede")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post("/notebook-chat/ask", json=_PAYLOAD)

        assert r.status_code == 500
        assert "falha de rede" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_question_returns_422(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/notebook-chat/ask", json={"notebook_title": "X"})

        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_notebook_title_returns_422(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/notebook-chat/ask", json={"notebook_title": "", "question": "x"})

        assert r.status_code == 422


_MOCK_STUDIO_RESPONSE = NotebookStudioItemsResponse(
    notes=[NoteInfo(id="n1", title="Nota", content="conteúdo")],
    reports=[ReportArtifactInfo(id="r1", title="relatorio.md", status=3, content="# md")],
)


class TestListStudioItemsEndpoint:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_success_returns_200_with_notes_and_reports(self, app):
        with patch(
            "app.routers.notebook_chat.list_studio_items",
            new=AsyncMock(return_value=_MOCK_STUDIO_RESPONSE),
        ) as m_list:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get("/notebook-chat/studio-items", params={"notebook_title": "Presidencia Funchal"})

        assert r.status_code == 200
        body = r.json()
        assert len(body["notes"]) == 1
        assert len(body["reports"]) == 1
        assert body["reports"][0]["content"] == "# md"
        m_list.assert_awaited_once_with("Presidencia Funchal")

    @pytest.mark.asyncio
    async def test_notebook_not_found_returns_404(self, app):
        with patch(
            "app.routers.notebook_chat.list_studio_items",
            new=AsyncMock(side_effect=NotebookNotFoundError("Nenhum notebook encontrado com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get("/notebook-chat/studio-items", params={"notebook_title": "X"})

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_ambiguous_title_returns_409(self, app):
        with patch(
            "app.routers.notebook_chat.list_studio_items",
            new=AsyncMock(side_effect=AmbiguousNotebookTitleError("Encontrados 2 notebooks com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get("/notebook-chat/studio-items", params={"notebook_title": "X"})

        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self, app):
        with patch(
            "app.routers.notebook_chat.list_studio_items",
            new=AsyncMock(side_effect=Exception("falha de rede")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get("/notebook-chat/studio-items", params={"notebook_title": "X"})

        assert r.status_code == 500
        assert "falha de rede" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_notebook_title_query_param_returns_422(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/notebook-chat/studio-items")

        assert r.status_code == 422


class TestDownloadReportEndpoint:
    @pytest.fixture
    def app(self):
        return _make_app()

    @pytest.mark.asyncio
    async def test_success_returns_200_with_markdown_file(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(
                return_value=("# conteúdo real".encode("utf-8"), "relatorio-andre-gabriel-rh.md", "text/markdown")
            ),
        ) as m_get:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download",
                    params={"notebook_title": "Presidencia Funchal", "artifact_id": "art-1"},
                )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert "attachment" in r.headers["content-disposition"]
        assert "relatorio-andre-gabriel-rh.md" in r.headers["content-disposition"]
        assert r.content == "# conteúdo real".encode("utf-8")
        m_get.assert_awaited_once_with("Presidencia Funchal", "art-1")

    @pytest.mark.asyncio
    async def test_pdf_artifact_returns_200_with_pdf_content_type(self, app):
        """Achado 2026-09-17: prompts de consolidação podem gerar PDF como artefato final — precisa funcionar igual."""
        pdf_bytes = b"%PDF-1.7 conteudo binario fake"
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(return_value=(pdf_bytes, "relatorio-diego-consolidado.pdf", "application/pdf")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download",
                    params={"notebook_title": "Presidencia Funchal", "artifact_id": "pdf-1"},
                )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        assert "relatorio-diego-consolidado.pdf" in r.headers["content-disposition"]
        assert r.content == pdf_bytes

    @pytest.mark.asyncio
    async def test_accented_filename_is_percent_encoded(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(return_value=(b"conteudo", "relatório com acento.md", "text/markdown")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download",
                    params={"notebook_title": "X", "artifact_id": "art-1"},
                )

        assert r.status_code == 200
        assert "filename*=UTF-8''" in r.headers["content-disposition"]

    @pytest.mark.asyncio
    async def test_notebook_not_found_returns_404(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(side_effect=NotebookNotFoundError("Nenhum notebook encontrado com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download", params={"notebook_title": "X", "artifact_id": "art-1"}
                )

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_ambiguous_title_returns_409(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(side_effect=AmbiguousNotebookTitleError("Encontrados 2 notebooks com o título 'X'.")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download", params={"notebook_title": "X", "artifact_id": "art-1"}
                )

        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_artifact_not_found_returns_404(self, app):
        from notebooklm.exceptions import ArtifactNotFoundError

        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(side_effect=ArtifactNotFoundError("art-inexistente")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download",
                    params={"notebook_title": "X", "artifact_id": "art-inexistente"},
                )

        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_content_unavailable_returns_502(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(side_effect=ArtifactContentUnavailableError("sem conteúdo por nenhuma via")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download", params={"notebook_title": "X", "artifact_id": "art-1"}
                )

        assert r.status_code == 502

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_500(self, app):
        with patch(
            "app.routers.notebook_chat.get_report_content",
            new=AsyncMock(side_effect=Exception("falha de rede")),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.get(
                    "/notebook-chat/reports/download", params={"notebook_title": "X", "artifact_id": "art-1"}
                )

        assert r.status_code == 500
        assert "falha de rede" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_artifact_id_returns_422(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/notebook-chat/reports/download", params={"notebook_title": "X"})

        assert r.status_code == 422
