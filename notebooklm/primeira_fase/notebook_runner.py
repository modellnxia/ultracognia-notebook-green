"""
notebooklm_runner.py
Comunica com o NotebookLM, injeta prompt proprietário oculto e gera relatório.

Requer:
  - notebooklm-py  (pip install notebooklm-py)
  - python-dotenv  (pip install python-dotenv)
  - Autenticação prévia via: notebooklm login
"""

import asyncio
import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from notebooklm import NotebookLMClient
from notebooklm.rpc.types import SlideDeckLength, SlideDeckFormat

# ─── Carrega variáveis de ambiente (.env) ───────────────────────────────────
load_dotenv()

# NUNCA exposto ao usuário final — vem do .env ou de um secrets manager
SECRET_PROMPT: str = os.getenv(
    "SECRET_PROMPT",
    "Analise os materiais com profundidade e estruture as respostas de forma clara.",
)
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "./outputs"))


# ─── Helpers ────────────────────────────────────────────────────────────────


def ensure_output_dir() -> Path:
    """Garante que o diretório de saída existe."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def build_hidden_prompt_text(user_context: str = "") -> str:
    """
    Monta o texto da 'fonte oculta' que injeta o prompt proprietário.
    O NotebookLM vai tratar isso como mais um documento de contexto.
    O usuário final nunca vê este conteúdo.
    """
    return f"""[INSTRUÇÕES DE SISTEMA — NÃO REFERENCIAR DIRETAMENTE]
{SECRET_PROMPT}

Contexto adicional da sessão:
{user_context if user_context else "Nenhum contexto adicional fornecido."}
"""


def timestamped_name(base: str) -> str:
    """Gera nome único com timestamp para evitar colisão de notebooks."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base}_{ts}"


# ─── Função principal ────────────────────────────────────────────────────────


async def generate_report(
    sources: list[dict],  # [{"type": "url"|"file"|"text", "value": "..."}]
    notebook_title: str = "Relatório Gerado",
    user_context: str = "",  # contexto livre do usuário (NÃO é o prompt secreto)
    generate_audio: bool = False,  # gera podcast
    generate_slides: bool = True,  # gera slideshow
    cleanup_notebook: bool = False,  # apaga o notebook ao final
) -> dict:
    """
    Fluxo completo:
      1. Cria notebook no NotebookLM
      2. Injeta prompt proprietário como fonte oculta
      3. Adiciona fontes do usuário
      4. Gera artefatos (áudio + slides)
      5. Baixa e salva localmente
      6. Retorna caminhos dos arquivos gerados
    """
    output_dir = ensure_output_dir()
    results = {
        "notebook_id": None,
        "notebook_title": None,
        "audio_path": None,
        "slides_path": None,
        "report_path": None,
        "summary": None,
    }

    async with await NotebookLMClient.from_storage() as client:

        # ── 1. Cria notebook ─────────────────────────────────────────────────
        nb_name = timestamped_name(notebook_title)
        print(f"[1/6] Criando notebook: '{nb_name}'...")
        nb = await client.notebooks.create(nb_name)
        results["notebook_id"] = nb.id
        results["notebook_title"] = nb_name
        print(f"      ✓ Notebook criado — ID: {nb.id}")

        # ── 2. Injeta prompt proprietário como fonte oculta ──────────────────
        # Estratégia: adicionar como fonte de texto simples.
        # O NotebookLM trata como contexto, o usuário nunca vê no produto final.
        print(f"[2/6] Injetando prompt proprietário (fonte oculta)...")
        hidden_text = build_hidden_prompt_text(user_context)
        hidden_text_source = await client.sources.add_text(
            nb.id,
            content=hidden_text,
            title="[config]",  # título neutro — não aparece em outputs
            wait=True,
        )
        print(f"      ✓ Prompt injetado silenciosamente.")

        # ── 3. Adiciona fontes do usuário ────────────────────────────────────
        print(f"[3/6] Adicionando {len(sources)} fonte(s) do usuário...")
        for i, source in enumerate(sources, 1):
            source_type = source.get("type", "url")
            source_value = source["value"]

            if source_type == "url":
                print(f"      [{i}] URL: {source_value}")
                await client.sources.add_url(nb.id, source_value, wait=True)

            elif source_type == "file":
                print(f"      [{i}] Arquivo: {source_value}")
                await client.sources.add_file(nb.id, source_value, wait=True)

            elif source_type == "text":
                print(f"      [{i}] Texto inline ({len(source_value)} chars)")
                await client.sources.add_text(
                    nb.id,
                    text=source_value,
                    title=source.get("title", f"Texto {i}"),
                    wait=True,
                )

        print(f"      ✓ Todas as fontes adicionadas.")

        # ── 4. Gera resumo textual via chat ──────────────────────────────────
        print(f"[4/6] Gerando resumo textual via chat...")
        """
        summary_response = await client.chat.ask(
            nb.id,
            question=(
                "Com base em todos os materiais, gere um relatório completo incluindo: "
                "1) Resumo executivo, "
                "2) Principais pontos e descobertas, "
                "3) Análise crítica, "
                "4) Conclusões e recomendações."
                "O slide deck deve conter apenas 2 slides"
            ),
        )
        results["summary"] = summary_response.answer
        

        # Salva relatório textual em Markdown
        report_path = output_dir / f"{nb_name}_relatorio.md"
        report_path.write_text(
            f"# {nb_name}\n\n"
            f"**Gerado em:** {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
            f"---\n\n"
            f"{summary_response.answer}",
            encoding="utf-8",
        )
        results["report_path"] = str(report_path)
        print(f"      ✓ Relatório salvo em: {report_path}")
        """
        # ── 5. Gera artefatos (áudio + slides) ──────────────────────────────
        if generate_audio:
            print(f"[5a/6] Gerando podcast/áudio overview...")
            # 'instructions' é onde o prompt influencia o ESTILO do áudio
            audio_status = await client.artifacts.generate_audio(
                nb.id,
                instructions="Seja envolvente, didático e destaque os pontos críticos.",
            )
            await client.artifacts.wait_for_completion(nb.id, audio_status.task_id)
            audio_path = output_dir / f"{nb_name}_podcast.mp3"
            await client.artifacts.download_audio(nb.id, str(audio_path))
            results["audio_path"] = str(audio_path)
            print(f"      ✓ Podcast salvo em: {audio_path}")

        if generate_slides:
            print(f"[5b/6] Gerando slide deck...")
            slide_status = await client.artifacts.generate_slide_deck(
                nb.id,
                slide_format=SlideDeckFormat.PRESENTER_SLIDES,
                slide_length=SlideDeckLength.SHORT,
                instructions="Crie apenas um artefato utilizando todos os sources disponiveis",
            )
            await client.artifacts.wait_for_completion(
                nb.id, slide_status.task_id, timeout=1200
            )
            slides_path = output_dir / f"{nb_name}_slides.pdf"
            await client.artifacts.download_slide_deck(nb.id, str(slides_path))
            results["slides_path"] = str(slides_path)
            print(f"      ✓ Slides salvos em: {slides_path}")
            print(" Removendo prompt oculto")
            await client.sources.delete(nb.id, hidden_text_source.id)
            print("      ✓ Prompt oculto removido")

        # ── 6. (Opcional) Limpa o notebook ──────────────────────────────────
        if cleanup_notebook:
            print(f"[6/6] Apagando notebook temporário...")
            await client.notebooks.delete(nb.id)
            print(f"      ✓ Notebook removido.")
        else:
            print(f"[6/6] Notebook mantido no NotebookLM (ID: {nb.id})")

    return results


# ─── Ponto de entrada ────────────────────────────────────────────────────────


async def main():
    """
    Exemplo de uso: gera relatório a partir de URLs + PDF local.
    Substitua pelas fontes reais do seu produto.
    """

    # Fontes que o USUÁRIO fornece (não contêm o prompt secreto)
    user_sources = [
        {
            "type": "url",
            "value": "https://arxiv.org/abs/2303.08774",  # GPT-4 Technical Report
        },
        {"type": "url", "value": "https://en.wikipedia.org/wiki/Large_language_model"},
        # Exemplo com arquivo local:
        # {"type": "file", "value": "./docs/meu_documento.pdf"},
        # Exemplo com texto direto:
        # {"type": "text", "value": "Conteúdo extra...", "title": "Notas"},
    ]

    results = await generate_report(
        sources=user_sources,
        notebook_title="Relatorio_LLM",
        user_context="Foco em aplicações empresariais de LLMs em 2025.",
        generate_audio=False,
        generate_slides=True,
        cleanup_notebook=False,  # True para deletar o notebook após gerar
    )

    # Exibe resultado final
    print("\n" + "=" * 60)
    print("RELATÓRIO GERADO COM SUCESSO")
    print("=" * 60)
    print(json.dumps(results, indent=2, ensure_ascii=False))

    if results["summary"]:
        print("\n── PRÉVIA DO RELATÓRIO ──")
        print(results["summary"][:500] + "...")


if __name__ == "__main__":
    asyncio.run(main())
