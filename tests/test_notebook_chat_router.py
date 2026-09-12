"""Testes de app/routers/notebook_chat.py — mesmo padrão de test_routers.py (app isolado, sem lifespan real)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.models.notebook_chat import NotebookChatResponse
from app.services.notebook_chat_service import AmbiguousNotebookTitleError, NotebookNotFoundError


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
