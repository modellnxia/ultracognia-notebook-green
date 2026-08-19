"""
Agente `structure` — a única etapa de IA de texto do pipeline (ver decisão
de 2026-08-18: como o editorial já chega pronto, colado pelo usuário, não
existe mais separação entre "narrative" e "structure"; essa etapa absorve
as duas coisas: interpretar o editorial inteiro E escolher o layout/montar
os blocos de conteúdo de cada slide).

O modelo nunca decide layout fora dos 6 permitidos (`LayoutId` é fechado —
ver models.py) nem cor fora do que ele mesmo define em `theme` — depois de
gerado, o `render` só consome o que está aqui, nunca improvisa.
"""

import json
import logging
from typing import Optional

import asyncpg
from pydantic import ValidationError

from app.decks.llm.client import generate_text
from app.decks.models import DeckSpec

logger = logging.getLogger(__name__)

MAX_STRUCTURE_ATTEMPTS = 3

# TODO(fatia futura): substituir esse guia textual pelas imagens de
# referência few-shot que o stakeholder vai fornecer (few-shot visual de
# layout) — ver decisão de 2026-08-18. Por enquanto, descrição em texto.
_LAYOUT_GUIDE = """Layouts disponíveis — escolha o mais adequado pra cada slide, NUNCA invente um layout novo:
- cover: slide de abertura, título grande, pouco conteúdo.
- section-break: transição entre blocos de assunto, só título.
- title-bullets: título + lista de pontos (no máximo 7 itens).
- two-column: título + conteúdo dividido em duas colunas (comparação, antes/depois).
- diagram-full: título + uma imagem/diagrama ocupando o slide inteiro.
- closing: slide de encerramento."""


def _build_prompt(editorial_text: str, previous_error: Optional[str] = None) -> str:
    schema = json.dumps(DeckSpec.model_json_schema(), ensure_ascii=False)
    correction = ""
    if previous_error:
        correction = (
            f"\n\nSua resposta anterior falhou a validação com este erro:\n{previous_error}\n"
            "Corrija e responda de novo — APENAS o JSON válido, nada mais.\n"
        )
    return f"""Você transforma um editorial de apresentação (texto livre, escrito por um humano) num documento estruturado (DeckSpec).

{_LAYOUT_GUIDE}

Regras:
- Separe o editorial em slides, na ordem em que aparecem no texto.
- Para cada slide, escolha o layout mais adequado ao conteúdo.
- Se o editorial descrever uma imagem pra algum slide, inclua um bloco "asset" com kind="image" e ref = a descrição da imagem, como escrita no editorial (será usada depois pra gerar a imagem de verdade).
- Se o editorial descrever um fluxo, processo, arquitetura ou relação entre etapas que fique mais claro como diagrama do que como texto, inclua um bloco "asset" com kind="diagram" e ref = a definição desse diagrama em sintaxe Mermaid válida (ex.: "flowchart LR\nA[Início] --> B[Meio] --> C[Fim]"), usando sempre um tipo simples de diagrama (flowchart ou sequenceDiagram) — nunca invente sintaxe fora do Mermaid.
- Escolha uma paleta de cores (theme.palette, hexadecimal, com bom contraste texto/fundo) e uma fonte (theme.font_stack) apropriadas ao tom do conteúdo.
- Responda APENAS com JSON válido, sem markdown, sem texto fora do JSON, seguindo este schema exatamente:

{schema}
{correction}
Editorial:
---
{editorial_text}
---"""


def _strip_markdown_fences(raw_text: str) -> str:
    """Remove cercas de código (```json ... ```) se o modelo as incluir por engano."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


async def generate_deck_structure(
    editorial_text: str, conn: asyncpg.Connection
) -> tuple[DeckSpec, int, int]:
    """
    Chama o LLM pra transformar o editorial em `DeckSpec`. Retenta até
    `MAX_STRUCTURE_ATTEMPTS` vezes, anexando o erro de validação ao prompt
    a cada nova tentativa.

    Retorna (deck, tokens_entrada_total, tokens_saida_total).
    """
    total_input_tokens = 0
    total_output_tokens = 0
    previous_error: Optional[str] = None

    for attempt in range(1, MAX_STRUCTURE_ATTEMPTS + 1):
        prompt = _build_prompt(editorial_text, previous_error)
        raw_text, input_tok, output_tok = await generate_text(prompt, conn)
        total_input_tokens += input_tok
        total_output_tokens += output_tok

        try:
            data = json.loads(_strip_markdown_fences(raw_text))
            deck = DeckSpec.model_validate(data)
            return deck, total_input_tokens, total_output_tokens
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "Tentativa %d/%d de gerar DeckSpec falhou na validação: %s",
                attempt, MAX_STRUCTURE_ATTEMPTS, exc,
            )
            previous_error = str(exc)

    raise ValueError(
        f"Não foi possível gerar um DeckSpec válido após {MAX_STRUCTURE_ATTEMPTS} tentativas. "
        f"Último erro: {previous_error}"
    )
