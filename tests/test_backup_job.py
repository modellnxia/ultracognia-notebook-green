"""
Unit tests for app/scheduler/backup_job.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.scheduler.backup_job import backup_notebooks_daily


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para montar o cenário de conexão mockada
# ─────────────────────────────────────────────────────────────────────────────


def _make_conn(
    user_ids=None,
    cached_notebook=None,
    messages=None,
):
    """
    Cria um AsyncMock de conexão configurado para os cenários de teste.
    - fetch: controlado por chamada (user_ids primeiro, depois messages)
    - fetchrow: retorna cached_notebook ou None
    - execute: no-op
    """
    conn = AsyncMock()
    conn.close = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=cached_notebook)

    if user_ids is None:
        user_ids = []
    if messages is None:
        messages = []

    # fetch é chamado com queries diferentes: primeiro para usuários, depois para mensagens
    user_rows = [{"user_id": uid} for uid in user_ids]
    message_rows = messages

    conn.fetch = AsyncMock(side_effect=[user_rows, message_rows])
    return conn


def _make_prepare_response(uid):
    from app.models.report import PrepareNotebookResponse
    return PrepareNotebookResponse(
        notebook_id=f"nb-{uid}",
        notebook_title=f"Backup Diário_20260521_230000",
        from_cache=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# backup_notebooks_daily
# ─────────────────────────────────────────────────────────────────────────────


class TestBackupNotebooksDaily:

    # ── Happy path: cria notebooks para todos os usuários ─────────────────

    @pytest.mark.asyncio
    async def test_creates_notebook_for_user_without_cache(self):
        uid = uuid.uuid4()
        msg_rows = [{"role": "user", "content": "oi", "created_at": None, "conversation_title": "c"}]
        conn = _make_conn(user_ids=[uid], cached_notebook=None, messages=msg_rows)

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch(
                "app.scheduler.backup_job.prepare_notebook",
                new=AsyncMock(return_value=_make_prepare_response(uid)),
            ),
        ):
            await backup_notebooks_daily()

        conn.execute.assert_called_once()  # save_notebook_id

    @pytest.mark.asyncio
    async def test_formats_messages_with_role_prefix(self):
        uid = uuid.uuid4()
        msg_rows = [
            {"role": "user", "content": "pergunta", "created_at": None, "conversation_title": "c"},
            {"role": "assistant", "content": "resposta", "created_at": None, "conversation_title": "c"},
        ]
        conn = _make_conn(user_ids=[uid], cached_notebook=None, messages=msg_rows)
        captured = {}

        async def capture_prepare(req, messages):
            captured["messages"] = messages
            return _make_prepare_response(uid)

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.prepare_notebook", new=AsyncMock(side_effect=capture_prepare)),
        ):
            await backup_notebooks_daily()

        assert captured["messages"][0] == "[USER] pergunta"
        assert captured["messages"][1] == "[ASSISTANT] resposta"

    # ── Cache hit: pula sem chamar o NotebookLM ────────────────────────────

    @pytest.mark.asyncio
    async def test_skips_user_with_existing_notebook(self):
        uid = uuid.uuid4()
        cached = {"notebook_id": "nb-existing", "notebook_title": "old", "report_content": None, "report_path": None}
        # fetch só é chamado 1x (para user_ids), pois o cache hit acontece antes do fetch de mensagens
        conn = AsyncMock()
        conn.close = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=cached)
        conn.fetch = AsyncMock(return_value=[{"user_id": uid}])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.prepare_notebook") as mock_prepare,
        ):
            await backup_notebooks_daily()

        mock_prepare.assert_not_called()
        conn.execute.assert_not_called()

    # ── Sem mensagens: pula o usuário ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_skips_user_without_messages(self):
        uid = uuid.uuid4()
        conn = _make_conn(user_ids=[uid], cached_notebook=None, messages=[])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.prepare_notebook") as mock_prepare,
        ):
            await backup_notebooks_daily()

        mock_prepare.assert_not_called()

    # ── Sem usuários: encerra sem criar nada ──────────────────────────────

    @pytest.mark.asyncio
    async def test_no_users_today_does_nothing(self):
        conn = AsyncMock()
        conn.close = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.prepare_notebook") as mock_prepare,
        ):
            await backup_notebooks_daily()

        mock_prepare.assert_not_called()

    # ── Isolamento de falha: erro em um usuário não para os demais ────────

    @pytest.mark.asyncio
    async def test_continues_after_individual_user_error(self):
        uid1, uid2 = uuid.uuid4(), uuid.uuid4()
        msg_rows = [{"role": "user", "content": "msg", "created_at": None, "conversation_title": "c"}]

        conn = AsyncMock()
        conn.close = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        # fetch: user_ids, mensagens uid1, mensagens uid2
        conn.fetch = AsyncMock(
            side_effect=[
                [{"user_id": uid1}, {"user_id": uid2}],
                msg_rows,
                msg_rows,
            ]
        )

        call_count = 0

        async def flaky_prepare(req, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("notebooklm down")
            return _make_prepare_response(uid2)

        with (
            patch("app.scheduler.backup_job.asyncpg.connect", new=AsyncMock(return_value=conn)),
            patch("app.scheduler.backup_job.prepare_notebook", new=AsyncMock(side_effect=flaky_prepare)),
        ):
            await backup_notebooks_daily()

        # O segundo usuário foi processado mesmo após o erro do primeiro
        assert call_count == 2
        conn.execute.assert_called_once()  # só uid2 foi salvo

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
