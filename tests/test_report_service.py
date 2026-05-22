"""
Unit tests for app/services/report_service.py (branch: main)
Coverage goal: 100%
"""
import uuid
from datetime import date
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.report import (
    PrepareNotebookRequest,
    ReportRequest,
)
from app.services.report_service import (
    _call_notebooklm_prepare,
    _ensure_output_dir,
    _join_messages,
    _timestamped_title,
    create_report,
    create_slides_from_notebook,
    orchestrate_prepare_notebook,
)


@asynccontextmanager
async def _noop_secret_prompt(client, nb_id):
    """Substituto do with_secret_prompt que não faz nada (sem acesso real ao LM)."""
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────────────


class TestHelpers:
    def test_timestamped_title_format(self):
        result = _timestamped_title("Relatorio")
        assert result.startswith("Relatorio_")
        assert len(result) > len("Relatorio_")

    def test_join_messages_separator(self):
        msgs = ["msg1", "msg2", "msg3"]
        result = _join_messages(msgs)
        assert "---" in result
        assert "msg1" in result
        assert "msg2" in result

    def test_join_messages_strips_blanks(self):
        result = _join_messages(["  ", "hello", ""])
        assert result == "hello"

    def test_ensure_output_dir_creates_path(self, tmp_path, monkeypatch):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path / "out")
        result = _ensure_output_dir()
        assert result.exists()


# ─────────────────────────────────────────────────────────────────────────────
# Fixture de client fake
# ─────────────────────────────────────────────────────────────────────────────


def _make_client_mock():
    """Monta um NotebookLMClient fake completo."""
    nb = MagicMock()
    nb.id = "new-nb-id-abc"

    config_src = MagicMock()
    config_src.id = "config-src-id"

    conv_src = MagicMock()
    conv_src.id = "conv-src-id"

    gen_status = MagicMock()
    gen_status.task_id = "task-id-xyz"

    final_status = MagicMock()
    final_status.is_failed = False

    client = MagicMock()
    client.notebooks.create = AsyncMock(return_value=nb)
    client.sources.add_text = AsyncMock(side_effect=[config_src, conv_src])
    client.sources.delete = AsyncMock()
    client.artifacts.generate_report = AsyncMock(return_value=gen_status)
    client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
    client.artifacts.download_report = AsyncMock()
    client.artifacts.generate_slide_deck = AsyncMock(return_value=gen_status)
    client.artifacts.download_slide_deck = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    return client


def _fake_from_storage(client):
    """Retorna uma coroutine que devolve o client fake."""
    async def fake():
        return client
    return fake


# ─────────────────────────────────────────────────────────────────────────────
# _call_notebooklm_prepare
# ─────────────────────────────────────────────────────────────────────────────


class TestPrepareNotebook:
    @pytest.fixture
    def req_args(self):
        return dict(
            user_id=uuid.uuid4(),
            target_date=date(2026, 5, 14),
        )

    @pytest.mark.asyncio
    async def test_creates_notebook_and_adds_sources(self, req_args):
        client = _make_client_mock()
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await _call_notebooklm_prepare(
                "Usuario Teste", req_args["target_date"], ["[USER] olá", "[ASSISTANT] oi"]
            )

        assert result.notebook_id == "new-nb-id-abc"
        assert result.from_cache is False
        client.notebooks.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_adds_conversation_source(self, req_args):
        client = _make_client_mock()
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            await _call_notebooklm_prepare(
                "Usuario Teste", req_args["target_date"], ["[USER] pergunta"]
            )

        all_calls = client.sources.add_text.call_args_list
        all_content = " ".join(
            str(c.kwargs.get("content", "") or (c.args[1] if len(c.args) > 1 else ""))
            for c in all_calls
        )
        assert "[USER] pergunta" in all_content

    @pytest.mark.asyncio
    async def test_returns_notebook_id(self, req_args):
        client = _make_client_mock()
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await _call_notebooklm_prepare(
                "Usuario Teste", req_args["target_date"], ["msg"]
            )

        assert result.notebook_id == "new-nb-id-abc"
        assert isinstance(result.notebook_title, str)

    @pytest.mark.asyncio
    async def test_notebook_title_format(self, req_args):
        """Verifica que o título segue o formato Nome_Usuario-Data."""
        client = _make_client_mock()
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await _call_notebooklm_prepare(
                "Henrique Freitas", date(2026, 5, 21), ["msg"]
            )

        assert result.notebook_title == "Henrique_Freitas-2026-05-21"


# ─────────────────────────────────────────────────────────────────────────────
# create_report
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateReport:
    @pytest.fixture
    def req(self):
        return ReportRequest(notebook_id="nb-123")

    @pytest.fixture
    def mock_conn(self):
        conn = AsyncMock()
        conn.execute = AsyncMock()
        return conn

    def _make_report_client(self, tmp_path):
        """Client que também escreve o arquivo esperado pelo download_report."""
        gen_status = MagicMock()
        gen_status.task_id = "task-report-xyz"

        final_status = MagicMock()
        final_status.is_failed = False

        async def fake_download(nb_id, output_path, artifact_id):
            Path(output_path).write_text("conteúdo do relatório gerado", encoding="utf-8")

        client = MagicMock()
        client.sources.add_text = AsyncMock()
        client.sources.delete = AsyncMock()
        client.artifacts.generate_report = AsyncMock(return_value=gen_status)
        client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
        client.artifacts.download_report = AsyncMock(side_effect=fake_download)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_generates_report_and_returns_response(self, req, tmp_path, monkeypatch, mock_conn):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)
        client = self._make_report_client(tmp_path)
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await create_report(mock_conn, req)

        assert result.notebook_id == "nb-123"
        assert "conteúdo do relatório gerado" in result.report

    @pytest.mark.asyncio
    async def test_calls_generate_report_with_custom_format(self, req, tmp_path, monkeypatch, mock_conn):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)
        from notebooklm.rpc import ReportFormat
        client = self._make_report_client(tmp_path)
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            await create_report(mock_conn, req)

        call_kwargs = client.artifacts.generate_report.call_args
        assert call_kwargs.kwargs.get("report_format") == ReportFormat.CUSTOM

    @pytest.mark.asyncio
    async def test_raises_on_failed_generation(self, req, tmp_path, monkeypatch, mock_conn):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)

        gen_status = MagicMock()
        gen_status.task_id = "task-fail"
        final_status = MagicMock()
        final_status.is_failed = True

        client = MagicMock()
        client.sources.add_text = AsyncMock()
        client.sources.delete = AsyncMock()
        client.artifacts.generate_report = AsyncMock(return_value=gen_status)
        client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.services.report_service.NotebookLMClient.from_storage",
                _fake_from_storage(client),
            ),
            pytest.raises(RuntimeError, match="Geração de relatório falhou"),
        ):
            await create_report(mock_conn, req)

    @pytest.mark.asyncio
    async def test_saves_report_to_disk(self, req, tmp_path, monkeypatch, mock_conn):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)
        client = self._make_report_client(tmp_path)
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await create_report(mock_conn, req)

        assert Path(result.report_path).exists()

    @pytest.mark.asyncio
    async def test_persists_report_in_database(self, req, tmp_path, monkeypatch, mock_conn):
        """Verifica que o relatório é persistido no banco após geração."""
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)
        client = self._make_report_client(tmp_path)
        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            await create_report(mock_conn, req)

        mock_conn.execute.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# create_slides_from_notebook
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateSlidesFromNotebook:
    @pytest.fixture
    def req(self):
        from app.models.report import NotebookRequest
        return NotebookRequest(notebook_id="nb-slides-01")

    @pytest.mark.asyncio
    async def test_generates_slides_and_returns_response(self, req, tmp_path, monkeypatch):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)

        gen_status = MagicMock()
        gen_status.task_id = "slide-task"

        final_status = MagicMock()
        final_status.is_failed = False

        client = MagicMock()
        client.sources.add_text = AsyncMock()
        client.sources.delete = AsyncMock()
        client.artifacts.generate_slide_deck = AsyncMock(return_value=gen_status)
        client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
        client.artifacts.download_slide_deck = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            result = await create_slides_from_notebook(req)

        assert result.notebook_id == "nb-slides-01"
        assert result.status is True

    @pytest.mark.asyncio
    async def test_downloads_slide_deck(self, req, tmp_path, monkeypatch):
        import app.services.report_service as svc
        monkeypatch.setattr(svc, "_OUTPUT_DIR", tmp_path)

        gen_status = MagicMock()
        gen_status.task_id = "slide-task"
        final_status = MagicMock()
        final_status.is_failed = False

        client = MagicMock()
        client.sources.add_text = AsyncMock()
        client.sources.delete = AsyncMock()
        client.artifacts.generate_slide_deck = AsyncMock(return_value=gen_status)
        client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
        client.artifacts.download_slide_deck = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.services.report_service.NotebookLMClient.from_storage",
            _fake_from_storage(client),
        ):
            await create_slides_from_notebook(req)

        client.artifacts.download_slide_deck.assert_called_once()
