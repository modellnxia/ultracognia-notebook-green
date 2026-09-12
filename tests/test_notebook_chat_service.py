"""Testes de app/services/notebook_chat_service.py — mesmo padrão de mock de test_report_service.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notebook_chat_service import (
    AmbiguousNotebookTitleError,
    NotebookNotFoundError,
    ask_notebook,
)


def _fake_notebook(id_: str, title: str) -> MagicMock:
    nb = MagicMock()
    nb.id = id_
    nb.title = title
    return nb


def _make_client_mock(notebooks: list, answer: str = "Resposta do NotebookLM.") -> MagicMock:
    ask_result = MagicMock()
    ask_result.answer = answer

    client = MagicMock()
    client.notebooks.list = AsyncMock(return_value=notebooks)
    client.chat.ask = AsyncMock(return_value=ask_result)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _patch_from_storage(client):
    """
    `from_storage()` é síncrono e devolve o próprio objeto usado como
    `async with` (idioma novo da lib, sem `await` antes — ver comentário em
    notebook_chat_service.py) — mock direto com `return_value`, sem
    coroutine no meio, diferente do `_fake_from_storage` de
    test_report_service.py (que mocka a forma antiga, com `await`).
    """
    return patch("app.services.notebook_chat_service.NotebookLMClient.from_storage", return_value=client)


class TestAskNotebook:
    @pytest.mark.asyncio
    async def test_returns_answer_when_title_matches_exactly(self):
        client = _make_client_mock(
            [_fake_notebook("nb-1", "Presidencia Funchal"), _fake_notebook("nb-2", "China rising")],
            answer="A presidência trata de X e Y.",
        )

        with _patch_from_storage(client):
            result = await ask_notebook("Presidencia Funchal", "Do que se trata?")

        assert result.answer == "A presidência trata de X e Y."
        client.chat.ask.assert_awaited_once_with("nb-1", "Do que se trata?")

    @pytest.mark.asyncio
    async def test_trims_whitespace_on_title_match(self):
        client = _make_client_mock([_fake_notebook("nb-1", "Presidencia Funchal")])

        with _patch_from_storage(client):
            result = await ask_notebook("  Presidencia Funchal  ", "x")

        assert result.answer

    @pytest.mark.asyncio
    async def test_raises_not_found_when_no_title_matches(self):
        client = _make_client_mock([_fake_notebook("nb-1", "China rising")])

        with _patch_from_storage(client):
            with pytest.raises(NotebookNotFoundError, match="Inexistente"):
                await ask_notebook("Inexistente", "x")

        client.chat.ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_ambiguous_when_title_matches_more_than_one(self):
        client = _make_client_mock(
            [_fake_notebook("nb-1", "Duplicado"), _fake_notebook("nb-2", "Duplicado")]
        )

        with _patch_from_storage(client):
            with pytest.raises(AmbiguousNotebookTitleError, match="Duplicado"):
                await ask_notebook("Duplicado", "x")

        client.chat.ask.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_title_match_is_case_sensitive(self):
        """Decisão deliberada (ver docstring do serviço): melhor 404 claro do que adivinhar o notebook errado."""
        client = _make_client_mock([_fake_notebook("nb-1", "Presidencia Funchal")])

        with _patch_from_storage(client):
            with pytest.raises(NotebookNotFoundError):
                await ask_notebook("presidencia funchal", "x")

    @pytest.mark.asyncio
    async def test_never_passes_conversation_id_or_source_ids(self):
        """Cada pergunta é isolada — sem continuidade de conversa, sem restrição de fontes (decisão do usuário)."""
        client = _make_client_mock([_fake_notebook("nb-1", "X")])

        with _patch_from_storage(client):
            await ask_notebook("X", "pergunta")

        client.chat.ask.assert_awaited_once_with("nb-1", "pergunta")
