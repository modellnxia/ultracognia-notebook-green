"""Unit tests for app/decks/repository.py — mesmo padrão de mock do resto do projeto."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.decks.repository import STEP_ORDER, DeckJobRepository


def _mock_transaction_conn() -> AsyncMock:
    """AsyncMock de asyncpg.Connection com `conn.transaction()` utilizável em `async with`."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


class TestCreateJob:
    @pytest.mark.asyncio
    async def test_inserts_job_and_all_steps(self):
        conn = _mock_transaction_conn()
        job_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "id": job_id, "user_id": user_id, "client_id": None,
            "editorial_text": "texto colado", "status": "pending",
            "cost_cents": 0, "created_at": None,
        }
        repo = DeckJobRepository(conn)

        job = await repo.create_job(user_id, "texto colado")

        assert job["id"] == job_id
        # 1 insert em deck_jobs (fetchrow) + N inserts em deck_job_steps (execute)
        assert conn.fetchrow.call_count == 1
        assert conn.execute.call_count == len(STEP_ORDER)
        inserted_steps = [call.args[2] for call in conn.execute.call_args_list]
        assert inserted_steps == STEP_ORDER

    @pytest.mark.asyncio
    async def test_passes_llm_provider_choice_to_insert(self):
        """Combo de escolha na tela (2026-08-20)."""
        conn = _mock_transaction_conn()
        job_id, user_id = uuid.uuid4(), uuid.uuid4()
        conn.fetchrow.return_value = {"id": job_id, "llm_provider": "deepseek"}
        repo = DeckJobRepository(conn)

        await repo.create_job(user_id, "texto colado", llm_provider="deepseek")

        insert_args = conn.fetchrow.call_args.args
        assert "deepseek" in insert_args

    @pytest.mark.asyncio
    async def test_llm_provider_defaults_to_none(self):
        conn = _mock_transaction_conn()
        job_id, user_id = uuid.uuid4(), uuid.uuid4()
        conn.fetchrow.return_value = {"id": job_id, "llm_provider": None}
        repo = DeckJobRepository(conn)

        await repo.create_job(user_id, "texto colado")

        insert_args = conn.fetchrow.call_args.args
        # ordem do INSERT: user_id, client_id, editorial_text, llm_provider, apply_style_guardrails
        assert insert_args[-2] is None  # llm_provider

    @pytest.mark.asyncio
    async def test_apply_style_guardrails_defaults_to_false(self):
        """
        Checkbox da tela — desligado por padrão (invertido em 2026-08-21 (4):
        achado real em produção, editorial com paleta própria clara sendo
        rejeitado mesmo com o checkbox aparentando desligado — o campo não
        estava chegando, caía no default antigo (true). Default do produto
        agora é "extrai do editorial, sem opinião de estilo nossa").
        """
        conn = _mock_transaction_conn()
        job_id, user_id = uuid.uuid4(), uuid.uuid4()
        conn.fetchrow.return_value = {"id": job_id, "apply_style_guardrails": False}
        repo = DeckJobRepository(conn)

        await repo.create_job(user_id, "texto colado")

        insert_args = conn.fetchrow.call_args.args
        assert insert_args[-1] is False  # apply_style_guardrails é o último parâmetro do INSERT

    @pytest.mark.asyncio
    async def test_passes_apply_style_guardrails_false(self):
        conn = _mock_transaction_conn()
        job_id, user_id = uuid.uuid4(), uuid.uuid4()
        conn.fetchrow.return_value = {"id": job_id, "apply_style_guardrails": False}
        repo = DeckJobRepository(conn)

        await repo.create_job(user_id, "texto colado", apply_style_guardrails=False)

        insert_args = conn.fetchrow.call_args.args
        assert insert_args[-1] is False


class TestListJobsForUser:
    @pytest.mark.asyncio
    async def test_queries_with_user_id_limit_and_offset(self):
        conn = _mock_transaction_conn()
        user_id = uuid.uuid4()
        conn.fetch.return_value = []
        repo = DeckJobRepository(conn)

        await repo.list_jobs_for_user(user_id, limit=10, offset=20)

        conn.fetch.assert_awaited_once()
        sql, *args = conn.fetch.call_args.args
        assert args == [user_id, 10, 20]
        assert "WHERE user_id = $1" in sql
        assert "ORDER BY created_at DESC" in sql

    @pytest.mark.asyncio
    async def test_uses_default_limit_and_offset(self):
        conn = _mock_transaction_conn()
        user_id = uuid.uuid4()
        conn.fetch.return_value = []
        repo = DeckJobRepository(conn)

        await repo.list_jobs_for_user(user_id)

        _, called_user_id, called_limit, called_offset = conn.fetch.call_args.args
        assert (called_user_id, called_limit, called_offset) == (user_id, 50, 0)

    @pytest.mark.asyncio
    async def test_returns_rows_from_db(self):
        conn = _mock_transaction_conn()
        job_id = uuid.uuid4()
        conn.fetch.return_value = [
            {"id": job_id, "status": "completed", "cost_cents": 0, "created_at": None, "editorial_preview": "abc…"},
        ]
        repo = DeckJobRepository(conn)

        rows = await repo.list_jobs_for_user(uuid.uuid4())

        assert len(rows) == 1
        assert rows[0]["id"] == job_id


class TestGetSteps:
    @pytest.mark.asyncio
    async def test_orders_by_pipeline_order_not_creation_order(self):
        conn = _mock_transaction_conn()
        job_id = uuid.uuid4()
        # Devolve fora de ordem de propósito — repo deve reordenar por STEP_ORDER
        conn.fetch.return_value = [
            {"step": "qa"}, {"step": "structure"}, {"step": "render"},
        ]
        repo = DeckJobRepository(conn)

        steps = await repo.get_steps(job_id)

        assert [s["step"] for s in steps] == ["structure", "render", "qa"]


class TestClaimNextPendingStep:
    @pytest.mark.asyncio
    async def test_returns_none_when_queue_empty(self):
        conn = _mock_transaction_conn()
        conn.fetchrow.return_value = None
        repo = DeckJobRepository(conn)

        result = await repo.claim_next_pending_step()

        assert result is None
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_claimed_step_as_running(self):
        conn = _mock_transaction_conn()
        step_id, job_id = uuid.uuid4(), uuid.uuid4()
        conn.fetchrow.return_value = {"id": step_id, "job_id": job_id, "step": "context"}
        repo = DeckJobRepository(conn)

        result = await repo.claim_next_pending_step()

        assert result["step"] == "context"
        # 2 updates: a etapa vira 'running', e o job vira 'running'
        assert conn.execute.call_count == 2


class TestMarkStepOutcome:
    @pytest.mark.asyncio
    async def test_mark_step_completed_writes_output_ref(self):
        conn = _mock_transaction_conn()
        repo = DeckJobRepository(conn)

        await repo.mark_step_completed(uuid.uuid4(), "fake://x", 10, 20, 5)

        conn.execute.assert_called_once()
        assert "completed" in conn.execute.call_args.args[0]

    @pytest.mark.asyncio
    async def test_mark_step_failed_writes_error(self):
        conn = _mock_transaction_conn()
        repo = DeckJobRepository(conn)

        await repo.mark_step_failed(uuid.uuid4(), "algo quebrou")

        conn.execute.assert_called_once()
        assert "failed" in conn.execute.call_args.args[0]


class TestFinalizeJobIfAllStepsDone:
    @pytest.mark.asyncio
    async def test_marks_job_completed_when_all_steps_completed(self):
        conn = _mock_transaction_conn()
        conn.fetch.return_value = [
            {"step": s, "status": "completed", "cost_cents": 1} for s in STEP_ORDER
        ]
        repo = DeckJobRepository(conn)

        await repo.finalize_job_if_all_steps_done(uuid.uuid4())

        conn.execute.assert_called_once()
        assert "completed" in conn.execute.call_args.args[0]

    @pytest.mark.asyncio
    async def test_marks_job_failed_when_any_step_failed(self):
        conn = _mock_transaction_conn()
        rows = [{"step": s, "status": "completed", "cost_cents": 0} for s in STEP_ORDER]
        rows[0] = {"step": STEP_ORDER[0], "status": "failed", "cost_cents": 0}
        conn.fetch.return_value = rows
        repo = DeckJobRepository(conn)

        await repo.finalize_job_if_all_steps_done(uuid.uuid4())

        conn.execute.assert_called_once()
        assert "failed" in conn.execute.call_args.args[0]

    @pytest.mark.asyncio
    async def test_does_nothing_while_steps_still_pending(self):
        conn = _mock_transaction_conn()
        rows = [{"step": s, "status": "completed", "cost_cents": 0} for s in STEP_ORDER]
        rows[-1] = {"step": STEP_ORDER[-1], "status": "pending", "cost_cents": 0}
        conn.fetch.return_value = rows
        repo = DeckJobRepository(conn)

        await repo.finalize_job_if_all_steps_done(uuid.uuid4())

        conn.execute.assert_not_called()


class TestEnforceCostCap:
    @pytest.mark.asyncio
    async def test_returns_true_and_does_nothing_when_within_budget(self):
        conn = _mock_transaction_conn()
        conn.fetch.return_value = [{"step": s, "cost_cents": 10} for s in STEP_ORDER]
        repo = DeckJobRepository(conn)

        within_budget = await repo.enforce_cost_cap(uuid.uuid4(), max_cost_cents=1000)

        assert within_budget is True
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_aborts_job_and_pending_steps_when_over_budget(self):
        conn = _mock_transaction_conn()
        conn.fetch.return_value = [{"step": s, "cost_cents": 100} for s in STEP_ORDER]
        repo = DeckJobRepository(conn)

        within_budget = await repo.enforce_cost_cap(uuid.uuid4(), max_cost_cents=50)

        assert within_budget is False
        assert conn.execute.call_count == 2  # marca job failed + marca etapas pending como failed
        assert "failed" in conn.execute.call_args_list[0].args[0]
        assert "failed" in conn.execute.call_args_list[1].args[0]
