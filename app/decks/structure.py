"""
Agente `structure` — a única etapa de IA de texto do pipeline (ver decisão
de 2026-08-18: como o editorial já chega pronto, colado pelo usuário, não
existe mais separação entre "narrative" e "structure"; essa etapa absorve
as duas coisas: interpretar o editorial inteiro E escolher o layout/montar
os blocos de conteúdo de cada slide).

O modelo nunca decide layout fora dos permitidos (`LayoutId` é fechado —
ver models.py) nem cor fora do que ele mesmo define em `theme` — depois de
gerado, o `render` só consome o que está aqui, nunca improvisa.

Few-shot visual (2026-08-19): o usuário comparou nosso resultado com decks
reais gerados pelo NotebookLM e considerou o nosso "inaceitável" — muito
simples, sem imagem (achado à parte: `DECK_IMAGE_PROVIDER` nem estava
configurado em produção) e sem a densidade visual dos exemplos. Duas
mudanças de estratégia nasceram disso: (1) o layout `infographic` — eyebrow
+ ilustração hero + painéis estruturados + citação, o padrão mais denso dos
exemplos; (2) as imagens de referência do próprio usuário (vendorizadas em
`static/fewshot/`) agora são anexadas de verdade na chamada ao Gemini
(multimodal — ver llm/client.py), fechando o TODO que só existia como texto
antes. Isso fecha o gap entre "descrição em texto do que eu quero" e
"aqui está literalmente o que eu quero, replica isso".

Multi-provider com escolha explícita (2026-08-20): o usuário quer comparar
Gemini/DeepSeek/OpenRouter de propósito (combo na tela do frontend) — ver
`preferred_provider` em `generate_deck_structure`. DeepSeek e OpenRouter não
recebem as imagens (só o Gemini é multimodal aqui, ver llm/client.py) —
pra não deixar esses dois sem NENHUM guia visual, `_FEWSHOT_DESCRIPTION`
descreve o mesmo padrão em texto, sempre incluída no prompt. Quando as
imagens também estão anexadas (Gemini), a descrição em texto só reforça —
não atrapalha.
"""

import asyncio
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

import asyncpg
from pydantic import ValidationError

from app.decks.llm.client import LLMError, generate_text
from app.decks.models import DeckSpec

logger = logging.getLogger(__name__)

MAX_STRUCTURE_ATTEMPTS = 3

# Pausa antes de retentar quando a falha foi da CHAMADA em si (rede, 5xx da
# API) — diferente da falha de formato (JSON/schema), aqui não adianta
# corrigir o prompt, só dar um respiro pro provider (ex.: 503 passageiro,
# já visto se repetir na prática).
_API_RETRY_BACKOFF_SECONDS = 3

_FEWSHOT_DIR = Path(__file__).parent / "static" / "fewshot"

_FEWSHOT_DESCRIPTION = """Descrição do padrão visual de referência (baseada em exemplos reais fornecidos pelo usuário). Se houver imagens anexadas a esta mensagem, elas SÃO esses exemplos — usam exatamente esse padrão, e a descrição abaixo só reforça o que elas já mostram. Se não houver imagem anexada (nem todo provider recebe imagem), esta descrição é o único guia — siga com a mesma atenção:
- Cada slide de referência tem uma etiqueta de categoria curta no topo (eyebrow, ex.: "Gestão de Capital Humano | Retenção"), um título em negrito, e um subtítulo de uma frase de contexto.
- Abaixo disso, UMA ilustração hero em estilo técnico/isométrico (tons escuros, iluminação dramática, uma metáfora visual específica pro conceito do slide — nunca uma foto genérica solta ou sem relação com o conteúdo).
- Abaixo da ilustração, 2 a 4 painéis estruturados lado a lado, cada um com um título curto em negrito e uma descrição curta.
- No rodapé, uma citação em itálico referenciando um framework/teoria de negócio real (ex.: "Operations Management — MIT Sloan", "Theory of Constraints — Goldratt").
- Isso é o padrão do layout "infographic", descrito em detalhe abaixo."""

_LAYOUT_GUIDE = """Layouts disponíveis — escolha o mais adequado pra cada slide, NUNCA invente um layout novo:
- cover: slide de abertura, título grande, pouco conteúdo.
- section-break: transição entre blocos de assunto, só título.
- title-bullets: título + lista de pontos (no máximo 7 itens).
- two-column: título + conteúdo dividido em duas colunas (comparação, antes/depois).
- diagram-full: título + uma imagem/diagrama ocupando o slide inteiro.
- closing: slide de encerramento.
- infographic: o padrão visual denso das imagens de referência anexadas a esta chamada — eyebrow (etiqueta de categoria) + título + subtítulo curto + UMA ilustração hero + 2 a 4 painéis estruturados (bloco "panels", cada painel com heading curto + texto curto) + citação de rodapé (framework/fonte). Use esse layout pra qualquer slide que apresente um conceito/argumento de negócio com peso — é o layout PADRÃO pra conteúdo analítico/estratégico, não uma exceção."""

# Rulebook compacto (2026-08-21) — destilado de 59 exemplos de referência
# reais catalogados um a um (ver FEWSHOT_ANALYSIS_METHOD.md e
# FEWSHOT_CATALOG_SHOT{1,2,3}.md — não vão pro prompt, só o resumo abaixo).
# Guardrail de dois níveis: as PEÇAS abaixo são um vocabulário fechado — a IA
# pode COMPOR uma combinação nova (nunca vista literalmente nos 59
# exemplos), mas só usando peças desta lista, nunca inventando uma peça
# nova. Isso é o que dá liberdade criativa real sem perder a identidade
# visual dos exemplos.
_COMPOSITION_GUIDE = """Peças de composição do layout "infographic" — pra cada slide "infographic", escolha (você decide, não precisa ser sempre a mesma combinação em todo slide):
- illustration_position: "left" (padrão mais comum) | "right" (inverte o lado — varie ao longo do deck, não deixe tudo do mesmo lado) | "background" (ilustração ocupa o slide inteiro, atrás do conteúdo centralizado — reserve pra slides de transição/fechamento com pouco texto) | "none" (sem ilustração — só quando o conteúdo já é denso o bastante em texto/painéis).
- eyebrow_style: "chamfered-left" | "pill-left" | "pill-center" (reserve pra quando illustration_position="background") | "none".
- panel_style: "void-frame" (moldura vazada, sem fundo) | "bordered-card" (fundo sutil + borda, o mais comum) | "borderless-icon" (sem moldura nenhuma).
- arrangement: "row" (fileira única, padrão) | "stacked" (empilhado verticalmente) | "comparison-columns" (pra comparar 2 opções/estados lado a lado) | "sequence-numbered" (passos 1-2-3) | "sequence-lettered" (passos A-B-C).
- citation_style: "bar-circuit-corners" (padrão) | "bordered-card" | "split-two".

Cada campo é INDEPENDENTE dos outros — combine livremente. Não repita sempre a mesma combinação em todos os slides do deck; varie de acordo com o que cada conteúdo pede (ex.: um slide de comparação pede arrangement="comparison-columns"; um slide de processo em etapas pede "sequence-numbered"). Se não tiver certeza, pode deixar um campo de fora (fica com o padrão) — mas evite deixar TODOS de fora em todos os slides, isso é o comportamento antigo que a gente está deixando pra trás."""


def _build_prompt(editorial_text: str, previous_error: Optional[str] = None) -> str:
    schema = json.dumps(DeckSpec.model_json_schema(), ensure_ascii=False)
    correction = ""
    if previous_error:
        correction = (
            f"\n\nSua resposta anterior falhou a validação com este erro:\n{previous_error}\n"
            "Corrija e responda de novo — APENAS o JSON válido, nada mais.\n"
        )
    return f"""Você transforma um editorial de apresentação (texto livre, escrito por um humano) num documento estruturado (DeckSpec).

{_FEWSHOT_DESCRIPTION}

Replique esse vocabulário visual sempre que o conteúdo do editorial pedir tratamento denso/analítico (layout "infographic", ver abaixo) — a ESTRUTURA da composição (eyebrow + título + subtítulo + ilustração + painéis + citação) segue o padrão descrito, mas você tem liberdade real de COMPOSIÇÃO dentro dele (ver peças abaixo) — não precisa (nem deve) repetir sempre a mesma combinação.

{_LAYOUT_GUIDE}

{_COMPOSITION_GUIDE}

Regras:
- Separe o editorial em slides, na ordem em que aparecem no texto.
- Para cada slide, escolha o layout mais adequado ao conteúdo — prefira "infographic" pra conteúdo analítico/estratégico (é o padrão nos exemplos anexados), reserve os outros layouts pra abertura, transição, comparação simples e encerramento.
- Ilustração é o comportamento padrão (todos os exemplos de referência têm alguma), mas não é regra técnica obrigatória: se um slide específico do editorial for genuinamente mais denso em texto/dados e não pedir imagem, pode usar illustration_position="none" ou simplesmente omitir "asset" — decida pelo conteúdo, não por hábito. Quando incluir, escreva uma descrição rica (composição, metáfora visual pro conceito do slide, estilo) em "asset" com kind="image", ou kind="diagram" (sintaxe Mermaid) quando o conteúdo for um fluxo/processo.
- Quando usar o layout "infographic": preencha "eyebrow" (categoria curta, ex.: "Gestão de Capital Humano | Retenção"), o primeiro bloco do "body" deve ser um parágrafo curto (subtítulo), e inclua um bloco "panels" com 2 a 4 painéis (cada um com heading curto + texto curto) resumindo os pontos-chave. Preencha "citation" com uma referência plausível a um framework/teoria de negócio real, no mesmo espírito dos exemplos (não precisa ser literal, mas precisa soar como uma citação real de gestão/economia/estratégia).
- Se o editorial descrever um fluxo, processo, arquitetura ou relação entre etapas que fique mais claro como diagrama do que como texto, inclua um bloco "asset" com kind="diagram" e ref = a definição desse diagrama em sintaxe Mermaid válida (ex.: "flowchart LR\\nA[Início] --> B[Meio] --> C[Fim]"), usando sempre um tipo simples de diagrama (flowchart ou sequenceDiagram) — nunca invente sintaxe fora do Mermaid.
- Escolha UMA paleta de cores (theme.palette, hexadecimal, com bom contraste texto/fundo) e UMA fonte (theme.font_stack) pro deck inteiro, coerentes com o tom do conteúdo — e mantenha essa mesma identidade visual em todas as descrições de ilustração que você escrever (mesma paleta/estilo mencionados em cada prompt de imagem), pra o deck inteiro parecer um conjunto único, não slides desconexos.
- `theme.palette.background` PRECISA ser um tom ESCURO (navy, petróleo, grafite, verde ou bordô bem escuros etc.) — nenhum dos exemplos de referência reais tem fundo claro/branco. Você escolhe livremente QUAL tom escuro combina com o tema do conteúdo (não precisa ser sempre o mesmo azul-marinho) — só não pode ser um tom claro. Isso é validado automaticamente; um fundo claro é rejeitado e você recebe o erro de volta pra corrigir.
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


@lru_cache(maxsize=1)
def _load_fewshot_images() -> list[tuple[bytes, str]]:
    """
    Carrega os exemplos visuais vendorizados em `static/fewshot/` (imagens
    reais fornecidas pelo usuário, comprimidas — ver README) — cacheado
    depois da primeira leitura, mesmo padrão de `diagrams.py::_mermaid_js_source`.
    Se a pasta não existir ou estiver vazia, devolve lista vazia (a chamada
    ao LLM simplesmente segue só com o guia em texto, sem quebrar nada).
    """
    if not _FEWSHOT_DIR.exists():
        logger.warning("Pasta de few-shot (%s) não encontrada — seguindo sem imagem de referência.", _FEWSHOT_DIR)
        return []
    images = []
    for path in sorted(_FEWSHOT_DIR.glob("*.jpg")):
        images.append((path.read_bytes(), "image/jpeg"))
    return images


async def generate_deck_structure(
    editorial_text: str,
    conn: asyncpg.Connection,
    *,
    preferred_provider: Optional[str] = None,
) -> tuple[DeckSpec, int, int]:
    """
    Chama o LLM pra transformar o editorial em `DeckSpec`. Retenta até
    `MAX_STRUCTURE_ATTEMPTS` vezes — por dois motivos distintos, tratados
    separado:
      - a CHAMADA em si falhou (`LLMError` — rede, 5xx da API, ex.: 503
        passageiro do Gemini, já visto acontecer de verdade): retenta com o
        MESMO prompt, depois de uma pausa curta. Não faz sentido "corrigir"
        o prompt aqui — a IA nem chegou a responder.
      - a RESPOSTA veio com formato errado (JSON quebrado, schema inválido):
        retenta anexando o erro ao prompt, pra a IA se corrigir.

    `preferred_provider`: escolha explícita do usuário (combo na tela,
    2026-08-20) — `None` mantém o fallback automático de sempre.

    Retorna (deck, tokens_entrada_total, tokens_saida_total).
    """
    total_input_tokens = 0
    total_output_tokens = 0
    previous_error: Optional[str] = None
    reference_images = _load_fewshot_images()

    for attempt in range(1, MAX_STRUCTURE_ATTEMPTS + 1):
        prompt = _build_prompt(editorial_text, previous_error)

        try:
            raw_text, input_tok, output_tok = await generate_text(
                prompt, conn, reference_images=reference_images, preferred_provider=preferred_provider
            )
        except LLMError as exc:
            logger.warning(
                "Tentativa %d/%d de chamar o LLM falhou (erro de API/rede, não de formato): %s",
                attempt, MAX_STRUCTURE_ATTEMPTS, exc,
            )
            if attempt < MAX_STRUCTURE_ATTEMPTS:
                await asyncio.sleep(_API_RETRY_BACKOFF_SECONDS)
                continue
            raise ValueError(
                f"O LLM falhou {MAX_STRUCTURE_ATTEMPTS}x seguidas (erro de API/rede). "
                f"Último erro: {exc}"
            ) from exc

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
