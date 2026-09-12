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

from notebooklm import NotebookLMClient

from app.models.notebook_chat import NotebookChatResponse

logger = logging.getLogger(__name__)


class NotebookNotFoundError(Exception):
    """Nenhum notebook encontrado com o título pedido."""


class AmbiguousNotebookTitleError(Exception):
    """Mais de um notebook encontrado com o mesmo título — não dá pra saber qual é o certo."""


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
