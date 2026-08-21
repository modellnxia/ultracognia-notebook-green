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

# Versão enxuta, sem opinião de estilo — usada quando `apply_style_guardrails=False`
# (2026-08-21, tarefa 1: checkbox na tela). O LLM ainda precisa saber o
# enum válido de layouts (é estrutural, sempre exigido pelo schema), mas
# sem a moldura "prefira infographic, é o padrão" — isso é opinião do
# nosso rulebook, não regra técnica.
_LAYOUT_GUIDE_MINIMAL = """Layouts disponíveis (enum fechado do schema, escolha o que fizer sentido pro conteúdo de cada slide — NUNCA invente um layout fora desta lista): cover, section-break, title-bullets, two-column, diagram-full, closing, infographic. "infographic" aceita eyebrow, painéis (bloco "panels", 2 a 4 itens) e citação de rodapé como campos opcionais — use como achar melhor."""

# Extração de campos já estruturados no editorial (2026-08-21, tarefa 1,
# extensão pedida pelo usuário depois de mandar um editorial de exemplo real
# com esse padrão) — SEMPRE incluída, independente do checkbox de estilo:
# não é opinião nossa de estética, é sobre respeitar conteúdo que o próprio
# usuário já escreveu explicitamente. Decisão do usuário: extração guiada
# por instrução de prompt (o LLM decide, não um parser determinístico) —
# mais flexível a variação de formatação, sem garantia 100%.
_EXTRACTION_GUIDE = """Extração de campos já prontos no editorial — IMPORTANTE:
Se o editorial já vier com campos claramente rotulados por slide (ex.: "Categoria:", "Título:", "Tagline de Ação:", "Layout Visual:", "Deep English Prompt:", "Texto Principal:", "Pós-Doutorado:" ou rótulos equivalentes), você deve COPIAR o valor desses campos literalmente pro JSON, não reescrever nem "melhorar":
- "Categoria" → "eyebrow" (cópia literal).
- "Título" → "title" (cópia literal).
- "Tagline de Ação" → primeiro bloco do "body" (parágrafo, cópia literal).
- "Layout Visual" (ex.: "Imagem na coluna esquerda (30%)") → "illustration_position" ("left" se mencionar esquerda, "right" se mencionar direita).
- "Deep English Prompt" (dentro da seção de imagem) → "asset.ref" com kind="image", **copiado literalmente, palavra por palavra** — o editorial já escreveu o prompt de imagem ideal, você NÃO deve reescrever ou resumir, só usar exatamente como está.
- "Texto Principal" (geralmente 2+ blocos de "título curto: descrição") → bloco "panels", um painel por item, heading = o título curto, text = a descrição.
- "Pós-Doutorado" ou "Rodapé"/nota de fonte acadêmica → "citation" (cópia literal ou próxima do literal).
Isso só se aplica quando o editorial realmente tem esse formato rotulado por slide. Pra editoriais em texto livre, sem essa estrutura, continue compondo normalmente a partir do conteúdo."""

# Piso de qualidade obrigatório (2026-08-21, tarefa 3) — nasceu de uma
# comparação real entre nosso resultado e um exemplo de referência real
# (NotebookLM) feita pelo usuário: nosso resultado tinha vão vertical vazio,
# painéis soltos sem conexão, cor de destaque quase invisível. Regras
# negativas concretas, não "capriche" genérico — só ativo com o guardrail
# ligado (é opinião de estilo nossa, não regra técnica).
_QUALITY_BAR = """PADRÃO OBRIGATÓRIO DE QUALIDADE — não é sugestão, é requisito mínimo:

Você tem liberdade total de CONTEÚDO (o assunto de cada slide vem do editorial, você decide o que falar) mas ZERO liberdade na densidade e no acabamento visual — o resultado tem que atingir o mesmo nível dos exemplos de referência, nunca um nível abaixo. Especificamente, é PROIBIDO:
- Escrever uma ilustração genérica de "banco de imagem" (foto-stock, render 3D liso, sem textura). A descrição da imagem precisa ter: iluminação dramática/cinematográfica nomeada (ex.: "moody cinematic lighting", "dramatic side lighting"), textura material explícita (concreto rachado, metal escovado, vidro, fibra), e uma metáfora visual que se CONECTA ao conceito do slide.
- Usar só 2 painéis quando o conteúdo do editorial sustentar mais. Se a informação permitir, prefira SEMPRE 3 ou 4 painéis a 2 — o mínimo de 2 é o piso, não a meta.
- Deixar os painéis como blocos soltos sem relação entre si quando o conteúdo tiver uma sequência lógica (causa→efeito, passo 1→2→3, antes→depois). Nesses casos, use arrangement="sequence-numbered" ou "sequence-lettered" — nunca "row" plano pra conteúdo sequencial.
- Escolher uma cor de destaque (theme.palette.accent) pouco saturada ou parecida demais com o fundo/texto. O accent precisa ser claramente distinto e vibrante — é ele que aparece na borda dos painéis, no eyebrow, nos elementos de destaque do texto. Se não dá pra notar a cor de relance olhando o slide, ela está fraca demais.
- Usar citation_style="bar-circuit-corners" (o padrão simples) quando o resto do slide já está denso — nesse caso prefira citation_style="bordered-card", fica mais integrado ao peso visual dos painéis.

O padrão de referência (ver imagens few-shot anexadas / descrição acima) é o piso de qualidade aceitável, não um teto — pense nele como "pelo menos tão bom quanto isso", nunca "mais simples que isso"."""

# Exemplo estrutural real (2026-08-21, tarefa 3, item 2) — em vez de só
# prosa prescritiva ("faça denso"), mostra um slide de verdade, no nível
# de qualidade esperado, com todas as peças de composição preenchidas.
# Construído a partir de um slide real de um editorial de exemplo que o
# usuário forneceu (não é conteúdo genérico inventado). Funciona melhor que
# prosa pra providers sem visão (DeepSeek) — "mostra", não só "descreve".
_STRUCTURE_EXAMPLE_JSON = """Exemplo de UM slide no nível de densidade/estrutura esperado (estude o padrão, não copie o conteúdo — o assunto do seu deck vem do editorial que você está processando agora):

{
  "layout": "infographic",
  "title": "A Sincronia do Mar e do Barco",
  "eyebrow": "ALINHAMENTO OPERACIONAL & LITERALIDADE DE NEGÓCIOS",
  "illustration_position": "left",
  "eyebrow_style": "pill-left",
  "panel_style": "bordered-card",
  "arrangement": "row",
  "citation_style": "bordered-card",
  "body": [
    {"type": "paragraph", "text": "Como indicadores macroeconômicos afetam diretamente o dia a dia e a sobrevivência do posto de trabalho na ponta da operação."},
    {"type": "panels", "panels": [
      {"heading": "O Impacto do Macro no Prato", "text": "Mudanças na Selic e na inflação não são conceitos abstratos; elas determinam a viabilidade financeira do cliente e o preço das parcelas."},
      {"heading": "Vigilância de Mercado", "text": "O colaborador precisa atuar como um 'Sócio do Contexto', desenvolvendo a capacidade de antecipar problemas do cliente antes do contato."}
    ]}
  ],
  "asset": {"kind": "image", "ref": "A surreal minimalist corporate illustration. A tiny, highly detailed business analyst in a navy suit stands inside an origami paper boat made of financial news text, navigating a turbulent ocean of neon-lit 3D line charts. Giant golden coins shaped like fish leap out of the chart waves. Moody cinematic lighting, deep blue and emerald color grading, hyper-realistic texture, 8k, professional BCG style render."},
  "citation": "Operations Management & Business Literacy (MIT Sloan): Aplicação da heurística de Disponibilidade Cognitiva."
}

Note: use 3-4 painéis quando o conteúdo do SEU editorial sustentar mais que 2 (esse exemplo específico tem 2 porque a fonte original só tinha 2 pontos — não é o teto)."""

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


def _build_prompt(
    editorial_text: str,
    previous_error: Optional[str] = None,
    *,
    apply_style_guardrails: bool = False,
) -> str:
    """
    `apply_style_guardrails` (2026-08-21, tarefa 1 — checkbox na tela):
      - True (padrão): rulebook completo entra no prompt — few-shot
        descritivo, guia de layout rico, peças de composição, piso de
        qualidade obrigatório, exemplo estrutural em JSON, e a regra de
        fundo escuro. É o comportamento de sempre.
      - False: nenhuma dessas opiniões de estilo entra — só o schema
        técnico (sempre obrigatório, garante JSON válido) e a extração de
        campos do editorial (essa sim sempre ativa, não é opinião nossa,
        é respeitar o que o usuário já escreveu). O LLM tem liberdade
        total de composição visual, inclusive escolher um fundo claro se
        o editorial pedir (a validação de luminância também é desligada
        pra este caso — ver `generate_deck_structure`).
    """
    schema = json.dumps(DeckSpec.model_json_schema(), ensure_ascii=False)
    correction = ""
    if previous_error:
        correction = (
            f"\n\nSua resposta anterior falhou a validação com este erro:\n{previous_error}\n"
            "Corrija e responda de novo — APENAS o JSON válido, nada mais.\n"
        )

    if apply_style_guardrails:
        style_block = f"""{_FEWSHOT_DESCRIPTION}

Replique esse vocabulário visual sempre que o conteúdo do editorial pedir tratamento denso/analítico (layout "infographic", ver abaixo) — a ESTRUTURA da composição (eyebrow + título + subtítulo + ilustração + painéis + citação) segue o padrão descrito, mas você tem liberdade real de COMPOSIÇÃO dentro dele (ver peças abaixo) — não precisa (nem deve) repetir sempre a mesma combinação.

{_LAYOUT_GUIDE}

{_COMPOSITION_GUIDE}

{_QUALITY_BAR}

{_STRUCTURE_EXAMPLE_JSON}"""
        layout_rule = 'prefira "infographic" pra conteúdo analítico/estratégico (é o padrão nos exemplos anexados), reserve os outros layouts pra abertura, transição, comparação simples e encerramento.'
        illustration_rule = 'Ilustração é o comportamento padrão (todos os exemplos de referência têm alguma), mas não é regra técnica obrigatória: se um slide específico do editorial for genuinamente mais denso em texto/dados e não pedir imagem, pode usar illustration_position="none" ou simplesmente omitir "asset" — decida pelo conteúdo, não por hábito.'
        background_rule = (
            '\n- `theme.palette.background` PRECISA ser um tom ESCURO (navy, petróleo, grafite, verde ou '
            "bordô bem escuros etc.) — nenhum dos exemplos de referência reais tem fundo claro/branco. Você "
            "escolhe livremente QUAL tom escuro combina com o tema do conteúdo (não precisa ser sempre o "
            "mesmo azul-marinho) — só não pode ser um tom claro. Isso é validado automaticamente; um fundo "
            "claro é rejeitado e você recebe o erro de volta pra corrigir."
        )
    else:
        style_block = _LAYOUT_GUIDE_MINIMAL
        layout_rule = "escolha o que fizer mais sentido pro conteúdo — sem preferência nenhuma imposta entre os layouts."
        illustration_rule = 'Ilustração é opcional — inclua um bloco "asset" só quando o conteúdo do slide pedir, decida livremente o estilo/composição da descrição.'
        background_rule = ""  # sem guardrail de estilo, sem exigência de fundo escuro — vale o que o editorial pedir (ou o LLM escolher livremente)

    return f"""Você transforma um editorial de apresentação (texto livre, escrito por um humano) num documento estruturado (DeckSpec).

{style_block}

{_EXTRACTION_GUIDE}

Regras:
- Separe o editorial em slides, na ordem em que aparecem no texto.
- Para cada slide, escolha o layout mais adequado ao conteúdo — {layout_rule}
- {illustration_rule} Quando incluir, escreva uma descrição rica (composição, metáfora visual pro conceito do slide, estilo) em "asset" com kind="image", ou kind="diagram" (sintaxe Mermaid) quando o conteúdo for um fluxo/processo.
- Quando usar o layout "infographic": preencha "eyebrow" (categoria curta, ex.: "Gestão de Capital Humano | Retenção"), o primeiro bloco do "body" deve ser um parágrafo curto (subtítulo), e inclua um bloco "panels" com 2 a 4 painéis (cada um com heading curto + texto curto) resumindo os pontos-chave. Preencha "citation" com uma referência plausível a um framework/teoria de negócio real, no mesmo espírito dos exemplos (não precisa ser literal, mas precisa soar como uma citação real de gestão/economia/estratégia).
- Se o editorial descrever um fluxo, processo, arquitetura ou relação entre etapas que fique mais claro como diagrama do que como texto, inclua um bloco "asset" com kind="diagram" e ref = a definição desse diagrama em sintaxe Mermaid válida (ex.: "flowchart LR\\nA[Início] --> B[Meio] --> C[Fim]"), usando sempre um tipo simples de diagrama (flowchart ou sequenceDiagram) — nunca invente sintaxe fora do Mermaid.
- Escolha UMA paleta de cores (theme.palette, hexadecimal, com bom contraste texto/fundo) e UMA fonte (theme.font_stack) pro deck inteiro, coerentes com o tom do conteúdo — e mantenha essa mesma identidade visual em todas as descrições de ilustração que você escrever (mesma paleta/estilo mencionados em cada prompt de imagem), pra o deck inteiro parecer um conjunto único, não slides desconexos.{background_rule}
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
    apply_style_guardrails: bool = False,
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

    `preferred_provider`: escolha explícita do usuário (combo na tela) —
    `None` mantém o fallback automático de sempre.

    `apply_style_guardrails` (2026-08-21, tarefa 1 — checkbox na tela):
    controla tanto o conteúdo do prompt (`_build_prompt`) quanto a
    validação de fundo escuro (`Theme.background_must_be_dark`, via
    `context=` no `model_validate` abaixo) — os dois precisam andar juntos,
    senão o LLM recebe instrução pra ser livre mas a validação continua
    travando ele de qualquer jeito. Também controla se as imagens de
    referência (few-shot vendorizadas) são anexadas — desligado, elas nem
    entram na chamada, pra não enviesar visualmente um pedido que é pra
    ser livre de opinião nossa de estilo.

    Retorna (deck, tokens_entrada_total, tokens_saida_total).
    """
    total_input_tokens = 0
    total_output_tokens = 0
    previous_error: Optional[str] = None
    reference_images = _load_fewshot_images() if apply_style_guardrails else []
    validation_context = {"enforce_dark_background": apply_style_guardrails}

    for attempt in range(1, MAX_STRUCTURE_ATTEMPTS + 1):
        prompt = _build_prompt(editorial_text, previous_error, apply_style_guardrails=apply_style_guardrails)

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
            deck = DeckSpec.model_validate(data, context=validation_context)
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
