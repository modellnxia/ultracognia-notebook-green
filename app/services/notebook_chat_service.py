"""
Serviço de chat isolado com um notebook do NotebookLM (2026-09-12).

Diferente do resto de app/services/report_service.py (que cria notebook,
adiciona fonte e gera artefatos de relatório/slides), este módulo só faz
UMA coisa: recebe um título de notebook já existente + uma pergunta, acha
o notebook por título (a lib não tem busca por título no servidor — só
client.notebooks.list() sem filtro, então filtramos aqui) e chama
client.chat.ask(notebook_id, question) — o "chat" normal do produto
NotebookLM, nunca usado neste projeto até agora.

Decisões confirmadas com o usuário antes de implementar (2026-09-12):
  - Cada pergunta é isolada — sem conversation_id, sem histórico entre
    chamadas (client.chat.ask cria/usa a conversa "atual" do notebook,
    mas este serviço nunca reaproveita o conversation_id retornado).
  - Nada de prompt/persona injetada (sem o with_secret_prompt usado em
    report_service.py) — pergunta "crua".
  - Sem streaming — client.chat.ask já devolve a resposta inteira de uma
    vez (AskResult.answer), então não tem nada a "esperar" além do
    round-trip HTTP normal.
  - Conta usada é sempre a mesma (eaespiritual@gmail.com, autenticada via
    master-token já configurado em app/core/notebooklm_auth.py) — não
    recebe user_id nem credencial nenhuma no request.
"""

import logging
import tempfile
from pathlib import Path

import httpx
from notebooklm import NotebookLMClient
from notebooklm._auth.cookies import build_httpx_cookies_from_storage
from notebooklm.exceptions import ArtifactNotFoundError

from app.models.notebook_chat import (
    NoteInfo,
    NotebookChatResponse,
    NotebookStudioItemsResponse,
    ReportArtifactInfo,
)

logger = logging.getLogger(__name__)


class NotebookNotFoundError(Exception):
    """Nenhum notebook encontrado com o título pedido."""


class AmbiguousNotebookTitleError(Exception):
    """Mais de um notebook encontrado com o mesmo título — não dá pra saber qual é o certo."""


class ArtifactContentUnavailableError(Exception):
    """Conteúdo do artefato não pôde ser obtido por nenhum dos métodos conhecidos (oficial ou fallback)."""


async def _find_notebook_id_by_title(client: NotebookLMClient, notebook_title: str) -> str:
    """
    A lib não expõe busca por título no servidor (só notebooks.list(), sem
    filtro nenhum) — lista tudo e filtra aqui, por igualdade exata (só
    tolera espaço em branco nas pontas, não maiúscula/minúscula: título
    errado deve dar 404 claro, não "adivinhar" o notebook errado).
    """
    notebooks = await client.notebooks.list()
    title_lookup = notebook_title.strip()
    matches = [nb for nb in notebooks if nb.title.strip() == title_lookup]

    if not matches:
        raise NotebookNotFoundError(f"Nenhum notebook encontrado com o título '{notebook_title}'.")
    if len(matches) > 1:
        raise AmbiguousNotebookTitleError(
            f"Encontrados {len(matches)} notebooks com o título '{notebook_title}' — "
            "ambíguo, não é possível saber qual usar com segurança."
        )
    return matches[0].id


async def ask_notebook(notebook_title: str, question: str) -> NotebookChatResponse:
    """Acha o notebook pelo título e faz UMA pergunta isolada, devolvendo só o texto da resposta."""
    # `async with NotebookLMClient.from_storage() as client:` — SEM `await` antes
    # do `from_storage()`, de propósito: essa é a forma nova/recomendada da lib
    # (a forma antiga, `async with await NotebookLMClient.from_storage() as client:`,
    # usada em report_service.py, já emite DeprecationWarning nesta mesma versão
    # 0.8.0 e será removida na v1.0 — não vale reproduzir isso em código novo).
    async with NotebookLMClient.from_storage() as client:
        notebook_id = await _find_notebook_id_by_title(client, notebook_title)
        logger.info("Chat isolado — notebook '%s' (id=%s)", notebook_title, notebook_id)

        result = await client.chat.ask(notebook_id, question)
        logger.debug(
            "Resposta recebida — notebook_id=%s, %d caractere(s)",
            notebook_id, len(result.answer),
        )

    return NotebookChatResponse(answer=result.answer)


async def _download_via_raw_url(client: NotebookLMClient, notebook_id: str, artifact_id: str) -> str:
    """
    Fallback (achado real, 2026-09-14) — usado quando `download_report()`
    falha pra um artefato de tipo não reconhecido por esta versão da lib
    (ver docstring de `_download_artifact_markdown`). A linha bruta da
    listagem de artefatos (`client.artifacts._list_raw`) carrega, numa
    posição não documentada/tipada oficialmente pela lib (índice 24 —
    `[nome, mime, url_visualização, url_download]`), a MESMA URL de
    download que o produto usa de verdade — testada manualmente: autenticando
    com os cookies da própria sessão (mesma função que a lib usa
    internamente), devolve o arquivo `.md` completo e íntegro.

    ⚠️ Não é API pública/documentada da lib — é dado bruto do RPC de
    listagem, lido por posição. Se o Google mudar o formato da resposta,
    isso pode quebrar sem aviso — por isso os `try/except` aqui e em
    `_download_artifact_markdown`: falha alto e claro
    (`ArtifactContentUnavailableError`), nunca silenciosamente devolve lixo.
    """
    raw_rows = await client.artifacts._list_raw(notebook_id)
    row = next((r for r in raw_rows if isinstance(r, list) and r and r[0] == artifact_id), None)

    try:
        download_url = row[24][3]
        if not isinstance(download_url, str) or not download_url.startswith("http"):
            raise ValueError("posição [24][3] não contém uma URL válida")
    except (TypeError, IndexError, ValueError) as exc:
        raise ArtifactContentUnavailableError(
            f"Não achei uma URL de download pro artefato {artifact_id} (formato de resposta inesperado)."
        ) from exc

    cookies = build_httpx_cookies_from_storage()
    async with httpx.AsyncClient(follow_redirects=True, timeout=60, cookies=cookies) as http_client:
        resp = await http_client.get(download_url)
        resp.raise_for_status()
        return resp.text


async def _download_artifact_markdown(client: NotebookLMClient, notebook_id: str, artifact_id: str) -> str:
    """
    Busca o conteúdo em markdown de um artefato, tentando primeiro o
    caminho **oficial** da lib (`client.artifacts.download_report`, o
    mesmo que `report_service.py` já usa — funciona pra relatórios
    "tradicionais", tipo REPORT) e caindo pro fallback (achado 2026-09-14,
    `_download_via_raw_url`) só quando o oficial falhar — é o caso real dos
    relatórios que o CHAT gera (tipo de artefato não mapeado nesta versão
    da lib, `ArtifactNotReadyError` mesmo com o artefato pronto).

    Levanta `ArtifactContentUnavailableError` se os dois falharem.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / f"{artifact_id}.md"
        try:
            await client.artifacts.download_report(notebook_id, str(tmp_path), artifact_id=artifact_id)
            return tmp_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.info(
                "download_report oficial falhou pro artefato %s — tentando fallback: %s", artifact_id, exc
            )

    try:
        return await _download_via_raw_url(client, notebook_id, artifact_id)
    except ArtifactContentUnavailableError:
        raise
    except Exception as exc:
        raise ArtifactContentUnavailableError(
            f"Não foi possível obter o conteúdo do artefato {artifact_id} por nenhum método conhecido."
        ) from exc


async def get_report_markdown(notebook_title: str, artifact_id: str) -> tuple[str, str]:
    """
    Busca o conteúdo em markdown de UM relatório específico (mesmo
    `artifact_id` que já vem em `GET /notebook-chat/studio-items`), pronto
    pra virar download — base do `GET /notebook-chat/reports/download`.

    `client.artifacts.get(notebook_id, artifact_id)` faz dois trabalhos de
    uma vez: confirma que o artefato existe (levanta `ArtifactNotFoundError`
    da própria lib se não existir) e confirma que ele pertence a ESSE
    notebook (não dá pra usar o id de um artefato de outro notebook).
    """
    async with NotebookLMClient.from_storage() as client:
        notebook_id = await _find_notebook_id_by_title(client, notebook_title)
        artifact = await client.artifacts.get(notebook_id, artifact_id)

        content = await _download_artifact_markdown(client, notebook_id, artifact_id)

    filename = artifact.title if artifact.title.endswith(".md") else f"{artifact.title}.md"
    return content, filename


async def list_studio_items(notebook_title: str) -> NotebookStudioItemsResponse:
    """
    Investigação (2026-09-14) — pedido do usuário pra verificar, ANTES de
    construir o endpoint de download de verdade, se a API dá acesso real
    aos relatórios que os prompts do chat geram na aba Studio (achado de
    2026-09-13: uma pergunta pedindo "crie um relatório na aba Studio"
    realmente cria um artefato novo no notebook, não é só resposta em texto).

    Lista dois tipos de conteúdo, porque não sabíamos qual dos dois a lib
    usa pra isso (Note ou Artifact tipo REPORT são mecanismos distintos —
    ver client.py da lib):
      - notes.list() — `Note.content` já vem inteiro na listagem, sem
        chamada extra (é o dado mais barato de confirmar).
      - artifacts.list() **sem filtro de tipo** — achado real (2026-09-14):
        os relatórios que o CHAT gera (pedindo "crie na aba Studio") vêm
        com um código de tipo interno (10) que esta versão da lib (0.8.0,
        sem correção confirmada em 0.8.1/0.8.2 — sem menção no CHANGELOG)
        não reconhece (`ArtifactType.UNKNOWN`) — `list_reports()` (que
        filtra só o tipo REPORT tradicional, código 2) simplesmente não os
        enxerga. Listar sem filtro pega os dois tipos. Conteúdo de cada um
        vem de `_download_artifact_markdown` (oficial + fallback, ver
        docstring dela) — se os dois falharem, `content` fica `None` (não
        derruba a listagem inteira), mas o item ainda aparece — sabemos que
        existe, só não conseguimos o conteúdo por nenhuma via ainda.
    """
    async with NotebookLMClient.from_storage() as client:
        notebook_id = await _find_notebook_id_by_title(client, notebook_title)

        notes = await client.notes.list(notebook_id)
        report_artifacts = await client.artifacts.list(notebook_id)
        logger.info(
            "Studio do notebook '%s' — %d nota(s), %d artefato(s)",
            notebook_title, len(notes), len(report_artifacts),
        )

        note_infos = [
            NoteInfo(id=n.id, title=n.title, created_at=n.created_at, content=n.content)
            for n in notes
        ]

        report_infos = []
        for artifact in report_artifacts:
            content = None
            try:
                content = await _download_artifact_markdown(client, notebook_id, artifact.id)
            except ArtifactContentUnavailableError as exc:
                logger.warning(
                    "Conteúdo indisponível pro relatório %s ('%s'): %s",
                    artifact.id, artifact.title, exc,
                )
            report_infos.append(
                ReportArtifactInfo(
                    id=artifact.id,
                    title=artifact.title,
                    status=artifact.status,
                    created_at=artifact.created_at,
                    url=artifact.url,
                    generation_prompt=artifact.generation_prompt,
                    content=content,
                )
            )

    return NotebookStudioItemsResponse(notes=note_infos, reports=report_infos)
