"""
Unit tests for app/services/report_service.py
Coverage goal: 100%
"""
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.report import NotebookRequest, ReportRequest, ReportResponse
from app.services import report_service
from app.services.report_service import (
    _build_system_source,
    _ensure_output_dir,
    _join_messages,
    _save_report,
    _timestamped_title,
    create_report,
    create_report_mock,
    create_slides_from_notebook,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestEnsureOutputDir:
    def test_creates_directory(self, tmp_path, monkeypatch):
        target = tmp_path / "outputs"
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", target)
        result = _ensure_output_dir()
        assert result == target
        assert target.is_dir()

    def test_idempotent_when_exists(self, tmp_path, monkeypatch):
        target = tmp_path / "outputs"
        target.mkdir()
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", target)
        result = _ensure_output_dir()
        assert result == target


class TestTimestampedTitle:
    def test_contains_title(self):
        result = _timestamped_title("Meu Relatório")
        assert result.startswith("Meu Relatório_")

    def test_contains_timestamp(self):
        result = _timestamped_title("T")
        parts = result.split("_", 1)
        assert len(parts) == 2
        # timestamp segment: YYYYMMDD_HHMMSS
        assert len(parts[1]) == 15

    def test_empty_title(self):
        result = _timestamped_title("")
        # should still produce a timestamp suffix
        assert "_" in result


class TestJoinMessages:
    def test_joins_with_separator(self):
        msgs = ["Hello", "World"]
        result = _join_messages(msgs)
        assert "Hello" in result
        assert "World" in result
        assert "---" in result

    def test_strips_whitespace(self):
        result = _join_messages(["  hi  ", "  bye  "])
        assert result.startswith("hi")

    def test_skips_blank_messages(self):
        result = _join_messages(["hello", "   ", "", "world"])
        assert "hello" in result
        assert "world" in result
        # blank entries should not appear as "---" gaps between empties
        assert result.count("---") == 1

    def test_single_message(self):
        result = _join_messages(["only"])
        assert result == "only"

    def test_all_blank_messages(self):
        result = _join_messages(["   ", "", "\t"])
        assert result == ""


class TestBuildSystemSource:
    def test_contains_header(self):
        result = _build_system_source()
        assert "INSTRUÇÕES DE SISTEMA" in result

    def test_contains_secret_prompt(self, monkeypatch):
        monkeypatch.setattr(report_service, "_SECRET_PROMPT", "MY_SECRET")
        result = _build_system_source()
        assert "MY_SECRET" in result


class TestSaveReport:
    def test_creates_md_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        path = _save_report("titulo_test", "conteúdo do relatório")
        assert path.exists()
        assert path.suffix == ".md"
        text = path.read_text(encoding="utf-8")
        assert "titulo_test" in text
        assert "conteúdo do relatório" in text

    def test_file_contains_generation_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        path = _save_report("t", "content")
        assert "Gerado em:" in path.read_text(encoding="utf-8")

    def test_returns_path_object(self, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = _save_report("x", "y")
        assert isinstance(result, Path)


# ─────────────────────────────────────────────────────────────────────────────
# create_report_mock
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateReportMock:
    @pytest.fixture
    def req(self):
        return ReportRequest(messages=["hello", "world"], notebook_title="TestReport")

    @pytest.mark.asyncio
    async def test_returns_report_response(self, req, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = await create_report_mock(req)
        assert isinstance(result, ReportResponse)

    @pytest.mark.asyncio
    async def test_uses_provided_notebook_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        req = ReportRequest(
            messages=["hi"], notebook_title="T", notebook_id="my-nb-id"
        )
        result = await create_report_mock(req)
        assert result.notebook_id == "my-nb-id"

    @pytest.mark.asyncio
    async def test_generates_default_notebook_id_when_none(self, req, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = await create_report_mock(req)
        assert result.notebook_id == "mock-notebook-id-123456789"

    @pytest.mark.asyncio
    async def test_report_contains_mock_sections(self, req, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = await create_report_mock(req)
        assert "Resumo executivo" in result.report
        assert "Análise crítica" in result.report
        assert "Conclusões" in result.report

    @pytest.mark.asyncio
    async def test_report_path_is_a_string(self, req, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = await create_report_mock(req)
        assert isinstance(result.report_path, str)

    @pytest.mark.asyncio
    async def test_file_is_persisted(self, req, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        result = await create_report_mock(req)
        assert Path(result.report_path).exists()

    @pytest.mark.asyncio
    async def test_title_defaults_used_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        # notebook_title has a default in the model, so pass it explicitly empty
        req = ReportRequest(messages=["m"], notebook_title="")
        result = await create_report_mock(req)
        # should still produce a valid timestamped title
        assert result.notebook_title != ""


# ─────────────────────────────────────────────────────────────────────────────
# create_report (real, with mocked NotebookLMClient)
# ─────────────────────────────────────────────────────────────────────────────


def _make_client_mock(
    nb_id="nb-001",
    task_id="task-001",
    config_source_id="src-config",
    conv_source_id="src-conv",
    report_text="Relatório gerado",
    is_failed=False,
    delete_raises=False,
):
    """Build a fully mocked NotebookLMClient context manager."""
    final_status = MagicMock()
    final_status.is_failed = is_failed

    nb = MagicMock()
    nb.id = nb_id

    config_source = MagicMock()
    config_source.id = config_source_id

    conv_source = MagicMock()
    conv_source.id = conv_source_id

    gen_status = MagicMock()
    gen_status.task_id = task_id

    client = AsyncMock()
    client.notebooks.create = AsyncMock(return_value=nb)
    client.sources.add_text = AsyncMock(
        side_effect=[config_source, conv_source]
    )
    client.artifacts.generate_report = AsyncMock(return_value=gen_status)
    client.artifacts.wait_for_completion = AsyncMock(return_value=final_status)
    client.artifacts.download_report = AsyncMock()
    client.artifacts.generate_slide_deck = AsyncMock(return_value=gen_status)
    client.artifacts.download_slide_deck = AsyncMock()

    if delete_raises:
        client.sources.delete = AsyncMock(side_effect=Exception("delete error"))
    else:
        client.sources.delete = AsyncMock()

    # context manager protocol
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    return client, cm, report_text


class TestCreateReport:
    def _patch_client(self, monkeypatch, tmp_path, **kwargs):
        client, cm, report_text = _make_client_mock(**kwargs)

        # from_storage is a coroutine (async classmethod) — must be AsyncMock
        async def fake_from_storage():
            return cm

        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(
            "app.services.report_service.NotebookLMClient.from_storage",
            fake_from_storage,
        )

        # make download_report write the report file
        async def fake_download(nb_id, output_path, artifact_id):
            Path(output_path).write_text(report_text, encoding="utf-8")

        client.artifacts.download_report = AsyncMock(side_effect=fake_download)
        return client

    @pytest.mark.asyncio
    async def test_creates_new_notebook_when_no_id(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(messages=["msg1"], notebook_title="NB")
        result = await create_report(req)
        client.notebooks.create.assert_called_once()
        assert result.notebook_id == "nb-001"

    @pytest.mark.asyncio
    async def test_reuses_existing_notebook_id(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(
            messages=["msg1"], notebook_title="NB", notebook_id="existing-nb"
        )
        result = await create_report(req)
        client.notebooks.create.assert_not_called()
        assert result.notebook_id == "existing-nb"

    @pytest.mark.asyncio
    async def test_injects_config_source_when_new_notebook(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(messages=["msg"], notebook_title="NB")
        await create_report(req)
        # first call → [config] source; second call → conversation source
        assert client.sources.add_text.call_count == 2
        first_call_kwargs = client.sources.add_text.call_args_list[0]
        assert first_call_kwargs.kwargs.get("title") == "[config]" or (
            len(first_call_kwargs.args) >= 3
            and first_call_kwargs.args[2] == "[config]"
        )

    @pytest.mark.asyncio
    async def test_does_not_inject_config_when_reusing_notebook(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(
            messages=["msg"], notebook_title="NB", notebook_id="reused"
        )
        await create_report(req)
        # only conversation source is added
        assert client.sources.add_text.call_count == 1

    @pytest.mark.asyncio
    async def test_deletes_config_source_after_report(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(messages=["m"], notebook_title="NB")
        await create_report(req)
        client.sources.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_failure_does_not_raise(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path, delete_raises=True)
        req = ReportRequest(messages=["m"], notebook_title="NB")
        # should not raise even if delete fails
        result = await create_report(req)
        assert result is not None

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_generation_fails(
        self, monkeypatch, tmp_path
    ):
        client = self._patch_client(monkeypatch, tmp_path, is_failed=True)
        req = ReportRequest(messages=["m"], notebook_title="NB")
        with pytest.raises(RuntimeError, match="Geração de relatório falhou"):
            await create_report(req)

    @pytest.mark.asyncio
    async def test_report_saved_locally(self, monkeypatch, tmp_path):
        self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(messages=["m"], notebook_title="NB")
        result = await create_report(req)
        assert Path(result.report_path).exists()

    @pytest.mark.asyncio
    async def test_response_contains_report_content(self, monkeypatch, tmp_path):
        self._patch_client(monkeypatch, tmp_path, report_text="conteudo_magico")
        req = ReportRequest(messages=["m"], notebook_title="NB")
        result = await create_report(req)
        assert "conteudo_magico" in result.report

    @pytest.mark.asyncio
    async def test_multiple_messages_are_joined(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = ReportRequest(messages=["a", "b", "c"], notebook_title="NB")
        await create_report(req)
        # conversation source add_text called with unified text containing all 3 messages
        last_call = client.sources.add_text.call_args_list[-1]
        content_arg = last_call.kwargs.get("content") or last_call.args[1]
        assert "a" in content_arg
        assert "b" in content_arg
        assert "c" in content_arg


# ─────────────────────────────────────────────────────────────────────────────
# create_slides_from_notebook
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateSlidesFromNotebook:
    def _patch_client(self, monkeypatch, tmp_path):
        gen_status = MagicMock()
        gen_status.task_id = "slide-task-01"

        client = AsyncMock()
        client.artifacts.generate_slide_deck = AsyncMock(return_value=gen_status)
        client.artifacts.wait_for_completion = AsyncMock(return_value=MagicMock())
        client.artifacts.download_slide_deck = AsyncMock()

        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(report_service, "_OUTPUT_DIR", tmp_path)

        # from_storage is async — must be awaitable
        async def fake_from_storage():
            return cm

        monkeypatch.setattr(
            "app.services.report_service.NotebookLMClient.from_storage",
            fake_from_storage,
        )
        return client

    @pytest.mark.asyncio
    async def test_returns_notebook_default_response(self, monkeypatch, tmp_path):
        self._patch_client(monkeypatch, tmp_path)
        req = NotebookRequest(notebook_id="nb-slides-01")
        result = await create_slides_from_notebook(req)
        assert result.notebook_id == "nb-slides-01"
        assert result.status is True

    @pytest.mark.asyncio
    async def test_calls_generate_slide_deck(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = NotebookRequest(notebook_id="nb-x")
        await create_slides_from_notebook(req)
        client.artifacts.generate_slide_deck.assert_called_once()

    @pytest.mark.asyncio
    async def test_waits_for_completion(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = NotebookRequest(notebook_id="nb-x")
        await create_slides_from_notebook(req)
        client.artifacts.wait_for_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_downloads_slide_deck(self, monkeypatch, tmp_path):
        client = self._patch_client(monkeypatch, tmp_path)
        req = NotebookRequest(notebook_id="nb-x")
        await create_slides_from_notebook(req)
        client.artifacts.download_slide_deck.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_message_in_response(self, monkeypatch, tmp_path):
        self._patch_client(monkeypatch, tmp_path)
        req = NotebookRequest(notebook_id="nb-x")
        result = await create_slides_from_notebook(req)
        assert "sucesso" in result.message.lower()
