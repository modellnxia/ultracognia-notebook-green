"""
Unit tests for app/decks/poller.py — orquestração isolada, sem banco real e
sem os sleeps dos executores falsos (mockados aqui pra rodar rápido).

A prova de retomada "de verdade" (matar um processo no meio e reiniciar com
outro processo, banco real) foi feita manualmente durante o desenvolvimento
desta fatia — ver histórico da conversa / CLAUDE.md. Estes testes cobrem a
lógica de orquestração em isolamento, pra segurança de regressão.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.decks import poller


_FAKE_CONN = object()  # sentinel — só pra verificar que o mesmo conn é repassado ao executor


async def _fake_get_db_conn():
    yield _FAKE_CONN


@pytest.fixture(autouse=True)
def patched_db_conn(monkeypatch):
    monkeypatch.setattr(poller, "get_db_conn", _fake_get_db_conn)


@pytest.fixture
def mock_repo(monkeypatch):
    repo = AsyncMock()
    repo.enforce_cost_cap.return_value = True  # dentro do orçamento, por padrão
    monkeypatch.setattr(poller, "DeckJobRepository", lambda conn: repo)
    return repo


class TestProcessNextStep:
    @pytest.mark.asyncio
    async def test_returns_false_when_queue_empty(self, mock_repo):
        mock_repo.claim_next_pending_step.return_value = None

        result = await poller.process_next_step()

        assert result is False

    @pytest.mark.asyncio
    async def test_runs_executor_and_marks_completed_on_success(self, mock_repo, monkeypatch):
        step_id, job_id = uuid.uuid4(), uuid.uuid4()
        mock_repo.claim_next_pending_step.return_value = {
            "id": step_id, "job_id": job_id, "step": "structure",
        }
        fast_executor = AsyncMock(return_value=("fake://structure/x", 1, 2, 3))
        monkeypatch.setattr(poller, "STEP_EXECUTORS", {"structure": fast_executor})

        result = await poller.process_next_step()

        assert result is True
        fast_executor.assert_awaited_once_with(job_id, _FAKE_CONN)
        mock_repo.mark_step_completed.assert_awaited_once_with(
            step_id, "fake://structure/x", 1, 2, 3
        )
        mock_repo.mark_step_failed.assert_not_awaited()
        mock_repo.enforce_cost_cap.assert_awaited_once()
        mock_repo.finalize_job_if_all_steps_done.assert_awaited_once_with(job_id)

    @pytest.mark.asyncio
    async def test_marks_failed_when_executor_raises(self, mock_repo, monkeypatch):
        step_id, job_id = uuid.uuid4(), uuid.uuid4()
        mock_repo.claim_next_pending_step.return_value = {
            "id": step_id, "job_id": job_id, "step": "render",
        }
        broken_executor = AsyncMock(side_effect=RuntimeError("boom"))
        monkeypatch.setattr(poller, "STEP_EXECUTORS", {"render": broken_executor})

        result = await poller.process_next_step()

        assert result is True  # processou algo, mesmo que tenha falhado
        mock_repo.mark_step_completed.assert_not_awaited()
        mock_repo.mark_step_failed.assert_awaited_once()
        assert "boom" in mock_repo.mark_step_failed.call_args.args[1]
        # finaliza o job mesmo apos falha — e' o que faz o job.status virar 'failed'
        mock_repo.finalize_job_if_all_steps_done.assert_awaited_once_with(job_id)

    @pytest.mark.asyncio
    async def test_stops_before_finalize_when_cost_cap_exceeded(self, mock_repo, monkeypatch):
        step_id, job_id = uuid.uuid4(), uuid.uuid4()
        mock_repo.claim_next_pending_step.return_value = {
            "id": step_id, "job_id": job_id, "step": "structure",
        }
        mock_repo.enforce_cost_cap.return_value = False  # estourou o teto
        fast_executor = AsyncMock(return_value=("ok", 1, 2, 3))
        monkeypatch.setattr(poller, "STEP_EXECUTORS", {"structure": fast_executor})

        result = await poller.process_next_step()

        assert result is True
        mock_repo.enforce_cost_cap.assert_awaited_once()
        mock_repo.finalize_job_if_all_steps_done.assert_not_awaited()


class TestPollOnce:
    @pytest.mark.asyncio
    async def test_swallows_unexpected_exceptions(self, monkeypatch):
        async def _boom():
            raise RuntimeError("erro inesperado")

        monkeypatch.setattr(poller, "process_next_step", _boom)

        await poller.poll_once()  # não deve propagar — só loga


class TestRegisterDeckPoller:
    def test_adds_interval_job_to_scheduler(self):
        scheduler = MagicMock()  # add_job é síncrono na API real do APScheduler

        poller.register_deck_poller(scheduler, interval_seconds=7)

        scheduler.add_job.assert_called_once()
        _, kwargs = scheduler.add_job.call_args
        assert kwargs["trigger"] == "interval"
        assert kwargs["seconds"] == 7
        assert kwargs["id"] == "deck_poller"
