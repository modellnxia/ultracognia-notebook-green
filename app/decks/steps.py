"""
Executores de cada etapa do pipeline. Todas as quatro já são reais desde
2026-08-18 (`qa` foi a última — ver qa.py, checks determinísticos, sem IA).
"""

import json
import logging
from typing import Awaitable, Callable
from uuid import UUID

import asyncpg

from app.decks.diagrams import render_diagram_png
from app.decks.llm.images import generate_image
from app.decks.models import DeckSpec
from app.decks.pdf import render_deck_pdf
from app.decks.pptx_render import render_deck_pptx
from app.decks.qa import check_deck
from app.decks.render import render_deck_html
from app.decks.storage import create_signed_url, ensure_bucket_exists, upload_object
from app.decks.structure import generate_deck_structure

logger = logging.getLogger(__name__)

StepResult = tuple[str, int, int, int]  # (output_ref, input_tokens, output_tokens, cost_cents)
StepExecutor = Callable[[UUID, asyncpg.Connection], Awaitable[StepResult]]


async def run_structure(job_id: UUID, conn: asyncpg.Connection) -> StepResult:
    """
    Lê o `editorial_text` colado pelo usuário (gravado na criação do job) e
    chama o LLM pra transformar em `DeckSpec` — layout, tema e blocos de
    conteúdo de cada slide, tudo numa chamada só (ver structure.py).
    """
    row = await conn.fetchrow("SELECT editorial_text, llm_provider FROM deck_jobs WHERE id = $1", job_id)
    if row is None:
        raise ValueError(f"Job {job_id} não encontrado.")

    deck, input_tokens, output_tokens = await generate_deck_structure(
        row["editorial_text"], conn, preferred_provider=row["llm_provider"]
    )
    output_ref = deck.model_dump_json()
    logger.info(
        "DeckSpec gerado pro job %s — %d slide(s), %d tokens entrada / %d saída",
        job_id, len(deck.slides), input_tokens, output_tokens,
    )
    # Custo em centavos calculado depois, quando soubermos o preço por
    # provider/modelo de verdade — 0 por enquanto, não bloqueia o pipeline.
    return output_ref, input_tokens, output_tokens, 0


async def _resolve_slide_asset(job_id: UUID, slide) -> None:
    """
    Resolve o `asset` de UM slide, in-place: `kind="image"` chama o Gemini;
    `kind="diagram"` renderiza Mermaid via Playwright. Nos dois casos, sobe o
    PNG resultante no Storage e troca `asset.ref` (que até aqui era um
    prompt/sintaxe em texto) pelo caminho do objeto no bucket.

    Resiliente por design (mesmo padrão do job de backup — `backup_job.py`):
    se ESTE asset falhar, loga e deixa o slide como estava (o `render` cai no
    placeholder de texto pra ele) em vez de derrubar o job inteiro por causa
    de um único slide problemático.

    Captura `Exception` largo de propósito, não só `ImageGenerationError`/
    `DiagramRenderError` — descoberto na validação real desta fatia: um 429
    (rate limit) do Gemini levanta `httpx.HTTPStatusError`, que não é
    nenhuma dessas duas e escapava por aqui, derrubando o job inteiro por
    causa de UM asset. Falha de rede/infra imprevista também não deve matar
    o job — mesma lógica de resiliência do backup job.
    """
    if slide.asset is None:
        return

    try:
        if slide.asset.kind == "image":
            image_bytes, mime_type = await generate_image(slide.asset.ref)
            ext = "png" if "png" in mime_type else mime_type.split("/")[-1]
        elif slide.asset.kind == "diagram":
            image_bytes = await render_diagram_png(slide.asset.ref)
            mime_type, ext = "image/png", "png"
        else:
            logger.warning("Slide %s com asset.kind desconhecido: %r — ignorando.", slide.id, slide.asset.kind)
            return
    except Exception as exc:
        logger.warning(
            "Falha ao resolver asset (%s) do slide %s do job %s — mantendo placeholder: %s",
            slide.asset.kind, slide.id, job_id, exc,
        )
        return

    object_path = f"{job_id}/assets/{slide.id}.{ext}"
    await upload_object(object_path, image_bytes, mime_type)
    slide.asset.ref = object_path
    logger.info("Asset (%s) do slide %s resolvido — %s", slide.asset.kind, slide.id, object_path)


async def run_assets(job_id: UUID, conn: asyncpg.Connection) -> StepResult:
    """
    Lê o `DeckSpec` produzido pela etapa `structure` e resolve cada asset
    descrito nele (imagem via Gemini, diagrama via Mermaid/Playwright — ver
    `_resolve_slide_asset`). Grava o `DeckSpec` atualizado (com os `ref`
    apontando pro Storage em vez de texto) como o resultado desta etapa —
    `render` consome essa versão, não a da etapa `structure` diretamente.
    """
    structure_row = await conn.fetchrow(
        "SELECT output_ref FROM deck_job_steps WHERE job_id = $1 AND step = 'structure'",
        job_id,
    )
    if structure_row is None or structure_row["output_ref"] is None:
        raise ValueError(f"Etapa 'structure' do job {job_id} ainda não tem resultado.")

    deck = DeckSpec.model_validate(json.loads(structure_row["output_ref"]))

    await ensure_bucket_exists()
    for slide in deck.slides:
        await _resolve_slide_asset(job_id, slide)

    return deck.model_dump_json(), 0, 0, 0


_PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


async def run_render(job_id: UUID, conn: asyncpg.Connection) -> StepResult:
    """
    Pega o `DeckSpec` já com os assets resolvidos (etapa `assets` — sempre
    roda antes desta, mesmo pra decks sem nenhum asset) e gera os DOIS
    formatos de saída — não é o usuário quem escolhe na hora de gerar,
    escolhe na hora de baixar (`GET /decks/{id}/download?format=pdf|pptx`,
    ver router.py). Como nenhum dos dois depende de LLM (PDF é Playwright,
    PPTX é `python-pptx`, os dois locais), gerar os dois sempre é
    praticamente de graça — mais barato que forçar o usuário a decidir
    antes de ver o resultado, ou reprocessar se mudar de ideia.

    `output_ref` desta etapa vira um JSON com os dois caminhos, não mais um
    caminho único (mudança de formato — não há dado antigo persistente pra
    migrar, esse output nunca sobreviveu além de um job).
    """
    assets_row = await conn.fetchrow(
        "SELECT output_ref FROM deck_job_steps WHERE job_id = $1 AND step = 'assets'",
        job_id,
    )
    if assets_row is None or assets_row["output_ref"] is None:
        raise ValueError(f"Etapa 'assets' do job {job_id} ainda não tem resultado.")

    deck = DeckSpec.model_validate(json.loads(assets_row["output_ref"]))
    deck_for_render = await _with_resolved_asset_urls(deck)

    await ensure_bucket_exists()

    html = render_deck_html(deck_for_render)
    pdf_bytes = await render_deck_pdf(html)
    pdf_path = f"{job_id}/deck.pdf"
    await upload_object(pdf_path, pdf_bytes, "application/pdf")
    logger.info("PDF do job %s salvo no storage — %s", job_id, pdf_path)

    pptx_bytes = await render_deck_pptx(deck_for_render)
    pptx_path = f"{job_id}/deck.pptx"
    await upload_object(pptx_path, pptx_bytes, _PPTX_CONTENT_TYPE)
    logger.info("PPTX do job %s salvo no storage — %s", job_id, pptx_path)

    output_ref = json.dumps({"pdf": pdf_path, "pptx": pptx_path})
    return output_ref, 0, 0, 0


async def _with_resolved_asset_urls(deck: DeckSpec) -> DeckSpec:
    """
    Devolve uma cópia do deck onde cada `asset.ref` que já é um caminho do
    Storage (resolvido pela etapa `assets`) vira uma URL assinada de verdade
    — é isso que o Chromium headless busca por HTTP ao renderizar o HTML.
    Assets não resolvidos (ainda em texto — falharam ou nunca existiram)
    passam intocados; o template cai no placeholder pra eles.
    """
    deck = deck.model_copy(deep=True)
    for slide in deck.slides:
        if slide.asset is not None and not slide.asset.ref.startswith("http"):
            # Só um caminho de Storage de verdade parece "{job_id}/assets/...".
            # Um prompt/sintaxe em texto não tem essa cara — não tenta assinar.
            if "/assets/" in slide.asset.ref:
                slide.asset.ref = await create_signed_url(slide.asset.ref)
    return deck


async def run_qa(job_id: UUID, conn: asyncpg.Connection) -> StepResult:
    """
    Roda os checks determinísticos (`qa.py::check_deck`) sobre o mesmo
    `DeckSpec` que o `render` usou (etapa `assets`, não a versão com URL
    assinada — QA não precisa que a imagem esteja acessível por HTTP, só
    precisa saber se ela foi resolvida ou não). Nunca falha o job por causa
    de PROBLEMAS encontrados — decisão do usuário em 2026-08-18: isso é só
    aviso, registrado no `output_ref` pro frontend decidir o que fazer.
    """
    assets_row = await conn.fetchrow(
        "SELECT output_ref FROM deck_job_steps WHERE job_id = $1 AND step = 'assets'",
        job_id,
    )
    if assets_row is None or assets_row["output_ref"] is None:
        raise ValueError(f"Etapa 'assets' do job {job_id} ainda não tem resultado.")

    deck = DeckSpec.model_validate(json.loads(assets_row["output_ref"]))
    report = await check_deck(deck)
    logger.info(
        "QA do job %s concluído — %d problema(s) encontrado(s)", job_id, report["issue_count"]
    )
    return json.dumps(report), 0, 0, 0


STEP_EXECUTORS: dict[str, StepExecutor] = {
    "structure": run_structure,
    "assets": run_assets,
    "render": run_render,
    "qa": run_qa,
}
