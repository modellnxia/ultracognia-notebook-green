"""
Testes de app/decks/structure.py — foco na lógica de retentativa, que tem
DOIS caminhos distintos (ver docstring de generate_deck_structure):
falha de CHAMADA (LLMError — rede/5xx) retenta com o mesmo prompt após uma
pausa; falha de FORMATO (JSON/schema) retenta corrigindo o prompt. Um bug
real (2026-08-19): a chamada ficava fora do try/except, um único LLMError
(ex.: 503 do Gemini) derrubava a etapa inteira sem nenhuma retentativa.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.decks import structure
from app.decks.llm.client import LLMError
from app.decks.models import DeckSpec, LayoutId, Slide, Theme


def _valid_deck_json() -> str:
    deck = DeckSpec(
        theme=Theme(
            palette={"primary": "#0F172A", "background": "#FFFFFF", "text": "#111111"},
            font_stack="Inter, sans-serif",
        ),
        slides=[Slide(layout=LayoutId.COVER, title="Capa")],
    )
    return deck.model_dump_json()


class TestGenerateDeckStructureApiRetry:
    @pytest.mark.asyncio
    async def test_retries_after_llm_error_and_succeeds(self):
        conn = object()
        mock_generate = AsyncMock(
            side_effect=[LLMError("503 Service Unavailable"), (_valid_deck_json(), 10, 20)]
        )

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure.asyncio.sleep", new=AsyncMock()) as m_sleep,
        ):
            deck, in_tok, out_tok = await structure.generate_deck_structure("editorial", conn)

        assert isinstance(deck, DeckSpec)
        assert (in_tok, out_tok) == (10, 20)
        assert mock_generate.await_count == 2
        m_sleep.assert_awaited_once()  # pausou uma vez, entre a 1a falha e a 2a tentativa

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts_all_llm_errors(self):
        conn = object()
        mock_generate = AsyncMock(side_effect=LLMError("503 Service Unavailable"))

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure.asyncio.sleep", new=AsyncMock()) as m_sleep,
        ):
            with pytest.raises(ValueError, match="erro de API/rede"):
                await structure.generate_deck_structure("editorial", conn)

        assert mock_generate.await_count == structure.MAX_STRUCTURE_ATTEMPTS
        # não pausa depois da ÚLTIMA tentativa (não há uma próxima retentativa esperando)
        assert m_sleep.await_count == structure.MAX_STRUCTURE_ATTEMPTS - 1

    @pytest.mark.asyncio
    async def test_does_not_accumulate_tokens_from_failed_llm_calls(self):
        """Uma chamada que falhou (LLMError) não retorna tokens — nada a somar dela."""
        conn = object()
        mock_generate = AsyncMock(
            side_effect=[LLMError("timeout"), (_valid_deck_json(), 5, 7)]
        )

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure.asyncio.sleep", new=AsyncMock()),
        ):
            _, in_tok, out_tok = await structure.generate_deck_structure("editorial", conn)

        assert (in_tok, out_tok) == (5, 7)  # só da chamada que teve sucesso


class TestGenerateDeckStructureValidationRetry:
    @pytest.mark.asyncio
    async def test_retries_on_invalid_json_and_succeeds(self):
        conn = object()
        mock_generate = AsyncMock(
            side_effect=[("isso não é json", 3, 4), (_valid_deck_json(), 10, 20)]
        )

        with patch("app.decks.structure.generate_text", mock_generate):
            deck, in_tok, out_tok = await structure.generate_deck_structure("editorial", conn)

        assert isinstance(deck, DeckSpec)
        assert (in_tok, out_tok) == (13, 24)  # das DUAS chamadas — as duas responderam de verdade
        assert mock_generate.await_count == 2

    @pytest.mark.asyncio
    async def test_second_attempt_prompt_includes_previous_error(self):
        conn = object()
        mock_generate = AsyncMock(
            side_effect=[("não é json válido", 1, 1), (_valid_deck_json(), 1, 1)]
        )

        with patch("app.decks.structure.generate_text", mock_generate):
            await structure.generate_deck_structure("editorial", conn)

        second_call_prompt = mock_generate.await_args_list[1].args[0]
        assert "falhou a validação" in second_call_prompt

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts_all_invalid(self):
        conn = object()
        mock_generate = AsyncMock(return_value=("não é json válido nenhuma vez", 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            with pytest.raises(ValueError, match="Não foi possível gerar um DeckSpec válido"):
                await structure.generate_deck_structure("editorial", conn)

        assert mock_generate.await_count == structure.MAX_STRUCTURE_ATTEMPTS

    @pytest.mark.asyncio
    async def test_strips_markdown_fences_before_parsing(self):
        conn = object()
        fenced = f"```json\n{_valid_deck_json()}\n```"
        mock_generate = AsyncMock(return_value=(fenced, 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            deck, *_ = await structure.generate_deck_structure("editorial", conn)

        assert isinstance(deck, DeckSpec)


class TestGenerateDeckStructureMixedFailures:
    @pytest.mark.asyncio
    async def test_llm_error_then_validation_error_then_success(self):
        conn = object()
        mock_generate = AsyncMock(
            side_effect=[
                LLMError("503"),
                ("json quebrado", 2, 2),
                (_valid_deck_json(), 5, 5),
            ]
        )

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure.asyncio.sleep", new=AsyncMock()),
        ):
            deck, in_tok, out_tok = await structure.generate_deck_structure("editorial", conn)

        assert isinstance(deck, DeckSpec)
        assert mock_generate.await_count == 3
        assert (in_tok, out_tok) == (7, 7)  # só as duas chamadas que responderam de verdade
