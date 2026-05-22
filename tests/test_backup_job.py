"""
Unit tests for app/scheduler/backup_job.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.scheduler.backup_job import backup_notebooks_daily


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_prepare_response(uid, from_cache=False):
    from app.models.report import PrepareNotebookResponse
    return PrepareNotebookResponse(
        notebook_id=f"nb-{uid}",
        notebook_title=f"Usuario_Teste-{date.today()}",
        from_cache=from_cache,
    )


def _make_conn(user_ids=None):
    """Cria um AsyncMock de conexão com fetch de user_ids configurado."""
    conn = AsyncMock()
    conn.close = AsyncMock()
    user_ids = user_ids or []
    user_rows = [{"user_id": uid} for uid in user_ids]
    conn.fetch = AsyncMock(return_value=user_rows)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# backup_notebooks_daily
# ─────────────────────────────────────────────────────────────────────────────


class TestBackupNotebooksDaily:

    # ── Happy path: cria notebooks para todos os usuários ─────────────────

    @pytest.mark.asyncio
    async def test_creates_notebook_for_user_without_cache(self):
        uid = uuid.uuid4()
        conn = _make_conn(user_ids=[uid])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch(
                "app.scheduler.backup_job.orchestrate_prepare_notebook",
                new=AsyncMock(return_value=_make_prepare_response(uid, from_cache=False)),
            ) as mock_orchestrate,
        ):
            await backup_notebooks_daily()

        mock_orchestrate.assert_called_once_with(
            conn=conn,
            user_id=uid,
            start_date=date.today(),
        )

    # ── Cache hit: skips user ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_skips_user_with_existing_notebook(self):
        uid = uuid.uuid4()
        conn = _make_conn(user_ids=[uid])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch(
                "app.scheduler.backup_job.orchestrate_prepare_notebook",
                new=AsyncMock(return_value=_make_prepare_response(uid, from_cache=True)),
            ) as mock_orchestrate,
        ):
            await backup_notebooks_daily()

        mock_orchestrate.assert_called_once()

    # ── Sem usuários: encerra sem criar nada ──────────────────────────────

    @pytest.mark.asyncio
    async def test_no_users_today_does_nothing(self):
        conn = _make_conn(user_ids=[])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.orchestrate_prepare_notebook") as mock_orchestrate,
        ):
            await backup_notebooks_daily()

        mock_orchestrate.assert_not_called()

    # ── Isolamento de falha: erro em um usuário não para os demais ────────

    @pytest.mark.asyncio
    async def test_continues_after_individual_user_error(self):
        uid1, uid2 = uuid.uuid4(), uuid.uuid4()
        conn = _make_conn(user_ids=[uid1, uid2])

        call_count = 0

        async def flaky_orchestrate(conn, user_id, start_date):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("notebooklm down")
            return _make_prepare_response(uid2, from_cache=False)

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch(
                "app.scheduler.backup_job.orchestrate_prepare_notebook",
                new=AsyncMock(side_effect=flaky_orchestrate),
            ),
        ):
            await backup_notebooks_daily()

        # O segundo usuário foi processado mesmo após o erro do primeiro
        assert call_count == 2

    # ── Conexão é sempre encerrada (even on error) ─────────────────────────

    @pytest.mark.asyncio
    async def test_closes_connection_even_on_unexpected_error(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("db caiu"))

        with patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError, match="db caiu"):
                await backup_notebooks_daily()

        conn.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler configuration
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateScheduler:
    def test_returns_asyncio_scheduler(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from app.scheduler.scheduler import create_scheduler
        scheduler = create_scheduler()
        assert isinstance(scheduler, AsyncIOScheduler)

    def test_has_backup_job_registered(self):
        from app.scheduler.scheduler import create_scheduler
        scheduler = create_scheduler()
        job_ids = [j.id for j in scheduler.get_jobs()]
        assert "backup_notebooks_daily" in job_ids

    def test_backup_job_uses_cron_trigger(self):
        from apscheduler.triggers.cron import CronTrigger
        from app.scheduler.scheduler import create_scheduler
        scheduler = create_scheduler()
        job = next(j for j in scheduler.get_jobs() if j.id == "backup_notebooks_daily")
        assert isinstance(job.trigger, CronTrigger)

    def test_scheduler_timezone_is_sao_paulo(self):
        from app.scheduler.scheduler import create_scheduler
        scheduler = create_scheduler()
        assert "Sao_Paulo" in str(scheduler.timezone)
