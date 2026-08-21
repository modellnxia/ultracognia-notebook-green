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
            palette={"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"},
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


class TestGenerateDeckStructurePreferredProvider:
    """Combo de escolha na tela (2026-08-20) — ver app/decks/llm/client.py."""

    @pytest.mark.asyncio
    async def test_passes_preferred_provider_through_to_generate_text(self):
        conn = object()
        mock_generate = AsyncMock(return_value=(_valid_deck_json(), 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            await structure.generate_deck_structure("editorial", conn, preferred_provider="deepseek")

        assert mock_generate.await_args.kwargs["preferred_provider"] == "deepseek"

    @pytest.mark.asyncio
    async def test_defaults_to_none_when_not_specified(self):
        conn = object()
        mock_generate = AsyncMock(return_value=(_valid_deck_json(), 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            await structure.generate_deck_structure("editorial", conn)

        assert mock_generate.await_args.kwargs["preferred_provider"] is None

    def test_prompt_always_includes_fewshot_description_text(self):
        """Reforço em texto pros providers que não recebem imagem (DeepSeek/OpenRouter não são multimodais aqui)."""
        prompt = structure._build_prompt("editorial qualquer")
        assert "eyebrow" in prompt.lower()
        assert "painéis" in prompt.lower() or "paineis" in prompt.lower()
        assert "citação" in prompt.lower() or "citacao" in prompt.lower()


class TestBuildPromptStyleGuardrailToggle:
    """Checkbox da tela (2026-08-21, tarefa 1) — ver FEWSHOT_RULEBOOK.md."""

    def test_guardrails_on_includes_rulebook_content(self):
        # prosa explicativa da _COMPOSITION_GUIDE — não confundir com nome de
        # campo do schema (esses aparecem sempre, o schema é estrutural e
        # sempre incluído, independente do checkbox).
        prompt = structure._build_prompt("editorial", apply_style_guardrails=True)
        assert "Cada campo é INDEPENDENTE dos outros" in prompt  # _COMPOSITION_GUIDE
        assert "PADRÃO OBRIGATÓRIO DE QUALIDADE" in prompt  # _QUALITY_BAR
        assert "PRECISA ser um tom ESCURO" in prompt  # regra de fundo escuro
        assert "Exemplo de UM slide" in prompt  # _STRUCTURE_EXAMPLE_JSON

    def test_guardrails_off_excludes_rulebook_content(self):
        prompt = structure._build_prompt("editorial", apply_style_guardrails=False)
        assert "Cada campo é INDEPENDENTE dos outros" not in prompt
        assert "PADRÃO OBRIGATÓRIO DE QUALIDADE" not in prompt
        assert "PRECISA ser um tom ESCURO" not in prompt
        assert "Exemplo de UM slide" not in prompt

    def test_guardrails_off_still_lists_valid_layout_enum(self):
        # estrutural (schema), não opinião de estilo — sempre presente
        prompt = structure._build_prompt("editorial", apply_style_guardrails=False)
        assert "infographic" in prompt
        assert "cover" in prompt

    def test_guardrails_on_and_off_both_include_extraction_guide(self):
        # extração de campos do editorial não é opinião de estilo — sempre ativa
        on_prompt = structure._build_prompt("editorial", apply_style_guardrails=True)
        off_prompt = structure._build_prompt("editorial", apply_style_guardrails=False)
        assert "Deep English Prompt" in on_prompt
        assert "Deep English Prompt" in off_prompt

    def test_guardrails_on_and_off_both_include_schema_and_editorial(self):
        # o JSON schema e o editorial em si SEMPRE entram, independente do checkbox
        for flag in (True, False):
            prompt = structure._build_prompt("meu editorial único", apply_style_guardrails=flag)
            assert "meu editorial único" in prompt
            assert '"DeckSpec"' in prompt or "DeckSpec" in prompt


class TestGenerateDeckStructureStyleGuardrailWiring:
    @pytest.mark.asyncio
    async def test_guardrails_off_skips_fewshot_images_and_relaxed_context(self, monkeypatch):
        conn = object()
        mock_generate = AsyncMock(return_value=(_valid_deck_json(), 1, 1))

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure._load_fewshot_images", return_value=[(b"x", "image/jpeg")]) as m_load,
        ):
            await structure.generate_deck_structure("editorial", conn, apply_style_guardrails=False)

        # imagens de referência não devem nem ser carregadas quando o guardrail está desligado
        assert mock_generate.await_args.kwargs["reference_images"] == []

    @pytest.mark.asyncio
    async def test_guardrails_on_loads_fewshot_images(self):
        conn = object()
        mock_generate = AsyncMock(return_value=(_valid_deck_json(), 1, 1))

        with (
            patch("app.decks.structure.generate_text", mock_generate),
            patch("app.decks.structure._load_fewshot_images", return_value=[(b"x", "image/jpeg")]),
        ):
            await structure.generate_deck_structure("editorial", conn, apply_style_guardrails=True)

        assert mock_generate.await_args.kwargs["reference_images"] == [(b"x", "image/jpeg")]

    @pytest.mark.asyncio
    async def test_guardrails_off_accepts_light_background_from_llm(self):
        """Ponta a ponta: LLM devolve fundo claro, guardrail desligado deixa passar."""
        conn = object()
        light_deck_json = json.dumps(
            {
                "version": "1.0",
                "theme": {
                    "palette": {"primary": "#0A192F", "background": "#F8FAFC", "text": "#111111"},
                    "font_stack": "Georgia",
                },
                "slides": [{"layout": "cover", "title": "X"}],
            }
        )
        mock_generate = AsyncMock(return_value=(light_deck_json, 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            deck, *_ = await structure.generate_deck_structure(
                "editorial", conn, apply_style_guardrails=False
            )

        assert deck.theme.palette["background"] == "#F8FAFC"

    @pytest.mark.asyncio
    async def test_guardrails_on_rejects_light_background_and_retries(self):
        """Mesmo cenário, guardrail ligado — deve rejeitar e retentar (não aceitar de primeira)."""
        conn = object()
        light_deck_json = json.dumps(
            {
                "version": "1.0",
                "theme": {
                    "palette": {"primary": "#0A192F", "background": "#F8FAFC", "text": "#111111"},
                    "font_stack": "Georgia",
                },
                "slides": [{"layout": "cover", "title": "X"}],
            }
        )
        mock_generate = AsyncMock(return_value=(light_deck_json, 1, 1))

        with patch("app.decks.structure.generate_text", mock_generate):
            with pytest.raises(ValueError, match="claro demais|Não foi possível gerar"):
                await structure.generate_deck_structure("editorial", conn, apply_style_guardrails=True)

        # tentou MAX_STRUCTURE_ATTEMPTS vezes, sempre recebendo o mesmo fundo claro
        assert mock_generate.await_count == structure.MAX_STRUCTURE_ATTEMPTS
