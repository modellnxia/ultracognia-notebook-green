"""Testes de app/services/notebook_chat_service.py — mesmo padrão de mock de test_report_service.py."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notebook_chat_service import (
    AmbiguousNotebookTitleError,
    ArtifactContentUnavailableError,
    NotebookNotFoundError,
    _download_artifact_markdown,
    _download_via_raw_url,
    ask_notebook,
    get_report_markdown,
    list_studio_items,
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


# ─────────────────────────────────────────────────────────────────────────────
# list_studio_items — investigação de 2026-09-14 (base do futuro endpoint de
# download): notes.list() já traz content inteiro; artifacts.list() (sem
# filtro de tipo — list_reports() perde os relatórios gerados por chat,
# achado real em 2026-09-14, ver docstring do serviço) só traz metadado,
# o conteúdo é baixado de verdade via download_report()
# (arquivo temporário, lido de volta como texto).
# ─────────────────────────────────────────────────────────────────────────────


def _fake_note(id_: str, title: str, content: str, created_at=None) -> MagicMock:
    n = MagicMock()
    n.id, n.title, n.content, n.created_at = id_, title, content, created_at
    return n


def _fake_report_artifact(
    id_: str, title: str, status: int = 3, created_at=None, url=None, generation_prompt=None
) -> MagicMock:
    a = MagicMock()
    a.id, a.title, a.status = id_, title, status
    a.created_at, a.url, a.generation_prompt = created_at, url, generation_prompt
    return a


def _make_studio_client_mock(
    notebooks: list, notes: list | None = None, report_artifacts: list | None = None,
    download_content_by_id: dict | None = None, download_side_effect=None,
) -> MagicMock:
    client = MagicMock()
    client.notebooks.list = AsyncMock(return_value=notebooks)
    client.notes.list = AsyncMock(return_value=notes or [])
    client.artifacts.list = AsyncMock(return_value=report_artifacts or [])

    if download_side_effect is not None:
        client.artifacts.download_report = AsyncMock(side_effect=download_side_effect)
    else:
        async def _fake_download_report(notebook_id, output_path, artifact_id=None, **kwargs):
            content = (download_content_by_id or {}).get(artifact_id, "")
            Path(output_path).write_text(content, encoding="utf-8")
            return output_path

        client.artifacts.download_report = AsyncMock(side_effect=_fake_download_report)

    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestListStudioItems:
    @pytest.mark.asyncio
    async def test_notes_come_back_with_content_inline_no_download_call(self):
        client = _make_studio_client_mock(
            [_fake_notebook("nb-1", "Presidencia Funchal")],
            notes=[_fake_note("note-1", "Rascunho de reunião", "conteúdo completo da nota aqui")],
        )

        with _patch_from_storage(client):
            result = await list_studio_items("Presidencia Funchal")

        assert len(result.notes) == 1
        assert result.notes[0].content == "conteúdo completo da nota aqui"
        client.artifacts.download_report.assert_not_awaited()  # notes não precisam de download

    @pytest.mark.asyncio
    async def test_report_artifacts_have_content_downloaded_for_real(self):
        client = _make_studio_client_mock(
            [_fake_notebook("nb-1", "Presidencia Funchal")],
            report_artifacts=[_fake_report_artifact("art-1", "relatorio-andre-gabriel-rh.md")],
            download_content_by_id={"art-1": "# Relatório André Gabriel\n\nConteúdo real baixado."},
        )

        with _patch_from_storage(client):
            result = await list_studio_items("Presidencia Funchal")

        assert len(result.reports) == 1
        assert result.reports[0].id == "art-1"
        assert result.reports[0].content == "# Relatório André Gabriel\n\nConteúdo real baixado."
        client.artifacts.download_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_content_is_none_when_download_fails(self):
        """Falha ao baixar UM relatório não derruba a listagem inteira — content fica None só pra ele."""
        client = _make_studio_client_mock(
            [_fake_notebook("nb-1", "X")],
            report_artifacts=[_fake_report_artifact("art-1", "Relatório com falha")],
            download_side_effect=RuntimeError("falha de rede"),
        )

        with _patch_from_storage(client):
            result = await list_studio_items("X")

        assert len(result.reports) == 1
        assert result.reports[0].content is None
        assert result.reports[0].title == "Relatório com falha"  # metadado continua presente

    @pytest.mark.asyncio
    async def test_chat_generated_report_still_listed_when_download_fails_with_artifact_not_ready(self):
        """
        Achado real (2026-09-14): relatórios gerados pelo chat têm um tipo
        de artefato (código 10) que download_report() não reconhece — falha
        com ArtifactNotReadyError de verdade, mesmo o artefato estando
        pronto (status=3). O item continua aparecendo na lista, só sem
        content — é isso que permite saber "existe" mesmo sem conseguir o
        conteúdo ainda.
        """
        from notebooklm.exceptions import ArtifactNotReadyError

        client = _make_studio_client_mock(
            [_fake_notebook("nb-1", "Presidencia Funchal")],
            report_artifacts=[_fake_report_artifact("art-1", "relatorio-andre-gabriel-rh.md")],
            download_side_effect=ArtifactNotReadyError("report", artifact_id="art-1"),
        )

        with _patch_from_storage(client):
            result = await list_studio_items("Presidencia Funchal")

        assert len(result.reports) == 1
        assert result.reports[0].title == "relatorio-andre-gabriel-rh.md"
        assert result.reports[0].content is None

    @pytest.mark.asyncio
    async def test_empty_studio_returns_empty_lists(self):
        client = _make_studio_client_mock([_fake_notebook("nb-1", "X")])

        with _patch_from_storage(client):
            result = await list_studio_items("X")

        assert result.notes == []
        assert result.reports == []

    @pytest.mark.asyncio
    async def test_raises_not_found_when_notebook_title_does_not_match(self):
        client = _make_studio_client_mock([_fake_notebook("nb-1", "Outro título")])

        with _patch_from_storage(client):
            with pytest.raises(NotebookNotFoundError):
                await list_studio_items("Inexistente")

    @pytest.mark.asyncio
    async def test_lists_multiple_notes_and_reports_together(self):
        client = _make_studio_client_mock(
            [_fake_notebook("nb-1", "X")],
            notes=[_fake_note("n1", "Nota 1", "a"), _fake_note("n2", "Nota 2", "b")],
            report_artifacts=[
                _fake_report_artifact("r1", "Relatório 1"),
                _fake_report_artifact("r2", "Relatório 2"),
            ],
            download_content_by_id={"r1": "conteúdo 1", "r2": "conteúdo 2"},
        )

        with _patch_from_storage(client):
            result = await list_studio_items("X")

        assert len(result.notes) == 2
        assert len(result.reports) == 2
        assert {r.content for r in result.reports} == {"conteúdo 1", "conteúdo 2"}


# ─────────────────────────────────────────────────────────────────────────────
# Download de verdade (2026-09-14/15) — endpoint GET /notebook-chat/reports/download.
# _download_via_raw_url usa a URL de download que vem na posição [24] da
# linha bruta de artifacts._list_raw (achado real, não documentado pela lib)
# — fallback só usado quando o caminho oficial (download_report) falha.
# ─────────────────────────────────────────────────────────────────────────────


def _raw_artifact_row(artifact_id: str, download_url: str | None = "https://download.url/x") -> list:
    """Linha bruta fake, no formato de artifacts._list_raw — só a posição [24] importa aqui."""
    tail = [["titulo.md", "text/markdown", "https://viewer.url", download_url]] if download_url else [None]
    return [artifact_id, "titulo.md", 10] + [None] * 21 + tail


def _fake_http_response(text: str, raise_error: Exception | None = None) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    if raise_error is not None:
        resp.raise_for_status = MagicMock(side_effect=raise_error)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _patch_http_get(response: MagicMock):
    fake_http_client = AsyncMock()
    fake_http_client.get = AsyncMock(return_value=response)
    fake_http_client.__aenter__ = AsyncMock(return_value=fake_http_client)
    fake_http_client.__aexit__ = AsyncMock(return_value=False)
    return (
        patch("app.services.notebook_chat_service.build_httpx_cookies_from_storage", return_value="fake-cookies"),
        patch("app.services.notebook_chat_service.httpx.AsyncClient", return_value=fake_http_client),
        fake_http_client,
    )


class TestDownloadViaRawUrl:
    @pytest.mark.asyncio
    async def test_fetches_content_from_url_at_position_24(self):
        client = MagicMock()
        client.artifacts._list_raw = AsyncMock(return_value=[_raw_artifact_row("art-1")])
        response = _fake_http_response("# conteúdo real do relatório")
        patch_cookies, patch_http, fake_http_client = _patch_http_get(response)

        with patch_cookies, patch_http:
            content = await _download_via_raw_url(client, "nb-1", "art-1")

        assert content == "# conteúdo real do relatório"
        fake_http_client.get.assert_awaited_once_with("https://download.url/x")

    @pytest.mark.asyncio
    async def test_raises_when_artifact_row_not_found(self):
        client = MagicMock()
        client.artifacts._list_raw = AsyncMock(return_value=[_raw_artifact_row("outro-id")])

        with pytest.raises(ArtifactContentUnavailableError):
            await _download_via_raw_url(client, "nb-1", "art-1")

    @pytest.mark.asyncio
    async def test_raises_when_position_24_has_no_valid_url(self):
        client = MagicMock()
        client.artifacts._list_raw = AsyncMock(return_value=[_raw_artifact_row("art-1", download_url=None)])

        with pytest.raises(ArtifactContentUnavailableError):
            await _download_via_raw_url(client, "nb-1", "art-1")

    @pytest.mark.asyncio
    async def test_raises_when_row_shorter_than_expected(self):
        client = MagicMock()
        client.artifacts._list_raw = AsyncMock(return_value=[["art-1", "titulo"]])  # linha curta demais

        with pytest.raises(ArtifactContentUnavailableError):
            await _download_via_raw_url(client, "nb-1", "art-1")


class TestDownloadArtifactMarkdown:
    @pytest.mark.asyncio
    async def test_uses_official_download_report_when_it_succeeds(self):
        client = MagicMock()

        async def _fake_download_report(notebook_id, output_path, artifact_id=None, **kwargs):
            Path(output_path).write_text("conteúdo oficial", encoding="utf-8")
            return output_path

        client.artifacts.download_report = AsyncMock(side_effect=_fake_download_report)

        with patch("app.services.notebook_chat_service._download_via_raw_url", new=AsyncMock()) as m_fallback:
            content = await _download_artifact_markdown(client, "nb-1", "art-1")

        assert content == "conteúdo oficial"
        m_fallback.assert_not_awaited()  # caminho oficial funcionou — fallback nem deve rodar

    @pytest.mark.asyncio
    async def test_falls_back_when_official_download_fails(self):
        client = MagicMock()
        client.artifacts.download_report = AsyncMock(side_effect=RuntimeError("ArtifactNotReadyError simulado"))

        with patch(
            "app.services.notebook_chat_service._download_via_raw_url",
            new=AsyncMock(return_value="conteúdo do fallback"),
        ) as m_fallback:
            content = await _download_artifact_markdown(client, "nb-1", "art-1")

        assert content == "conteúdo do fallback"
        m_fallback.assert_awaited_once_with(client, "nb-1", "art-1")

    @pytest.mark.asyncio
    async def test_raises_content_unavailable_when_both_paths_fail(self):
        client = MagicMock()
        client.artifacts.download_report = AsyncMock(side_effect=RuntimeError("falhou"))

        with patch(
            "app.services.notebook_chat_service._download_via_raw_url",
            new=AsyncMock(side_effect=ArtifactContentUnavailableError("também falhou")),
        ):
            with pytest.raises(ArtifactContentUnavailableError):
                await _download_artifact_markdown(client, "nb-1", "art-1")


class TestGetReportMarkdown:
    @pytest.mark.asyncio
    async def test_happy_path_returns_content_and_filename(self):
        artifact = MagicMock()
        artifact.title = "relatorio-andre-gabriel-rh.md"

        client = _make_client_mock([_fake_notebook("nb-1", "Presidencia Funchal")])
        client.artifacts.get = AsyncMock(return_value=artifact)

        with (
            _patch_from_storage(client),
            patch(
                "app.services.notebook_chat_service._download_artifact_markdown",
                new=AsyncMock(return_value="# conteúdo do relatório"),
            ),
        ):
            content, filename = await get_report_markdown("Presidencia Funchal", "art-1")

        assert content == "# conteúdo do relatório"
        assert filename == "relatorio-andre-gabriel-rh.md"  # já tinha .md, não duplica
        client.artifacts.get.assert_awaited_once_with("nb-1", "art-1")

    @pytest.mark.asyncio
    async def test_appends_md_extension_when_title_lacks_it(self):
        artifact = MagicMock()
        artifact.title = "Relatório sem extensão"

        client = _make_client_mock([_fake_notebook("nb-1", "X")])
        client.artifacts.get = AsyncMock(return_value=artifact)

        with (
            _patch_from_storage(client),
            patch(
                "app.services.notebook_chat_service._download_artifact_markdown",
                new=AsyncMock(return_value="conteúdo"),
            ),
        ):
            _, filename = await get_report_markdown("X", "art-1")

        assert filename == "Relatório sem extensão.md"

    @pytest.mark.asyncio
    async def test_raises_notebook_not_found_before_touching_artifacts(self):
        client = _make_client_mock([_fake_notebook("nb-1", "Outro título")])
        client.artifacts.get = AsyncMock()

        with _patch_from_storage(client):
            with pytest.raises(NotebookNotFoundError):
                await get_report_markdown("Inexistente", "art-1")

        client.artifacts.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_propagates_artifact_not_found_error_from_lib(self):
        from notebooklm.exceptions import ArtifactNotFoundError

        client = _make_client_mock([_fake_notebook("nb-1", "X")])
        client.artifacts.get = AsyncMock(side_effect=ArtifactNotFoundError("art-inexistente"))

        with _patch_from_storage(client):
            with pytest.raises(ArtifactNotFoundError):
                await get_report_markdown("X", "art-inexistente")

    @pytest.mark.asyncio
    async def test_propagates_content_unavailable_error(self):
        artifact = MagicMock()
        artifact.title = "X.md"
        client = _make_client_mock([_fake_notebook("nb-1", "X")])
        client.artifacts.get = AsyncMock(return_value=artifact)

        with (
            _patch_from_storage(client),
            patch(
                "app.services.notebook_chat_service._download_artifact_markdown",
                new=AsyncMock(side_effect=ArtifactContentUnavailableError("sem conteúdo")),
            ),
        ):
            with pytest.raises(ArtifactContentUnavailableError):
                await get_report_markdown("X", "art-1")
