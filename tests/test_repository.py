"""
Unit tests for app/repositories/conversations.py and app/repositories/notebooks.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.conversations import ConversationMessageRepository
from app.repositories.notebooks import NotebookRepository


# ─────────────────────────────────────────────────────────────────────────────
# ConversationMessageRepository
# ─────────────────────────────────────────────────────────────────────────────


class TestConversationMessageRepository:
    @pytest.fixture
    def user_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def target_date(self):
        return date(2026, 5, 18)

    @pytest.fixture
    def mock_conn(self):
        conn = AsyncMock()
        conn.fetch = AsyncMock()
        return conn

    @pytest.fixture
    def repo(self, mock_conn):
        return ConversationMessageRepository(mock_conn)

    # ── Happy path ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_rows_from_db(self, repo, mock_conn, user_id, target_date):
        expected = [
            {"role": "user", "content": "hello", "created_at": None, "conversation_title": "conv"}
        ]
        mock_conn.fetch.return_value = expected
        result = await repo.fetch_messages_by_user_and_date(user_id, target_date)
        assert result == expected

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rows(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetch.return_value = []
        result = await repo.fetch_messages_by_user_and_date(user_id, target_date)
        assert result == []

    @pytest.mark.asyncio
    async def test_calls_fetch_with_correct_args(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetch.return_value = []
        await repo.fetch_messages_by_user_and_date(user_id, target_date)
        args = mock_conn.fetch.call_args
        assert user_id in args.args
        assert target_date in args.args

    @pytest.mark.asyncio
    async def test_sql_contains_status_filter(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetch.return_value = []
        await repo.fetch_messages_by_user_and_date(user_id, target_date)
        sql = mock_conn.fetch.call_args.args[0]
        assert "status" in sql
        assert "'ok'" in sql

    @pytest.mark.asyncio
    async def test_sql_orders_by_created_at(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetch.return_value = []
        await repo.fetch_messages_by_user_and_date(user_id, target_date)
        sql = mock_conn.fetch.call_args.args[0]
        assert "ORDER BY" in sql
        assert "created_at" in sql

    @pytest.mark.asyncio
    async def test_returns_multiple_rows(self, repo, mock_conn, user_id, target_date):
        rows = [
            {"role": "user", "content": f"msg {i}", "created_at": None, "conversation_title": "c"}
            for i in range(5)
        ]
        mock_conn.fetch.return_value = rows
        result = await repo.fetch_messages_by_user_and_date(user_id, target_date)
        assert len(result) == 5

    def test_stores_connection(self, mock_conn):
        repo = ConversationMessageRepository(mock_conn)
        assert repo.conn is mock_conn


# ─────────────────────────────────────────────────────────────────────────────
# NotebookRepository
# ─────────────────────────────────────────────────────────────────────────────


class TestNotebookRepository:
    @pytest.fixture
    def user_id(self):
        return uuid.uuid4()

    @pytest.fixture
    def target_date(self):
        return date(2026, 5, 14)

    @pytest.fixture
    def mock_conn(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock()
        conn.execute = AsyncMock()
        return conn

    @pytest.fixture
    def repo(self, mock_conn):
        return NotebookRepository(mock_conn)

    # ── get_notebook_by_user_and_date ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_returns_row_when_found(self, repo, mock_conn, user_id, target_date):
        expected = {
            "notebook_id": "nb-123",
            "notebook_title": "Titulo",
            "report_content": None,
            "report_path": None,
        }
        mock_conn.fetchrow.return_value = expected
        result = await repo.get_notebook_by_user_and_date(user_id, target_date)
        assert result == expected

    @pytest.mark.asyncio
    async def test_get_returns_none_when_not_found(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetchrow.return_value = None
        result = await repo.get_notebook_by_user_and_date(user_id, target_date)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_calls_fetchrow_with_correct_args(self, repo, mock_conn, user_id, target_date):
        mock_conn.fetchrow.return_value = None
        await repo.get_notebook_by_user_and_date(user_id, target_date)
        args = mock_conn.fetchrow.call_args.args
        assert user_id in args
        assert target_date in args

    # ── save_notebook_id ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_notebook_id_calls_execute(self, repo, mock_conn, user_id, target_date):
        await repo.save_notebook_id(user_id, "nb-abc", "Titulo", target_date)
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_notebook_id_passes_correct_args(self, repo, mock_conn, user_id, target_date):
        await repo.save_notebook_id(user_id, "nb-abc", "Titulo", target_date)
        args = mock_conn.execute.call_args.args
        assert user_id in args
        assert "nb-abc" in args
        assert "Titulo" in args
        assert target_date in args

    @pytest.mark.asyncio
    async def test_save_notebook_id_sql_has_on_conflict(self, repo, mock_conn, user_id, target_date):
        await repo.save_notebook_id(user_id, "nb-abc", "Titulo", target_date)
        sql = mock_conn.execute.call_args.args[0]
        assert "ON CONFLICT" in sql

    # ── update_notebook_report ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_update_report_calls_execute(self, repo, mock_conn, user_id, target_date):
        await repo.update_notebook_report(user_id, target_date, "conteudo", "/path/rel.md")
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_report_passes_correct_args(self, repo, mock_conn, user_id, target_date):
        await repo.update_notebook_report(user_id, target_date, "conteudo", "/path/rel.md")
        args = mock_conn.execute.call_args.args
        assert "conteudo" in args
        assert "/path/rel.md" in args
        assert user_id in args
        assert target_date in args

    @pytest.mark.asyncio
    async def test_update_report_sql_is_update(self, repo, mock_conn, user_id, target_date):
        await repo.update_notebook_report(user_id, target_date, "c", "/p")
        sql = mock_conn.execute.call_args.args[0]
        assert sql.strip().upper().startswith("UPDATE")

    # ── save_notebook (full) ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_notebook_calls_execute(self, repo, mock_conn, user_id, target_date):
        await repo.save_notebook(user_id, "nb-abc", "Titulo", target_date, "conteudo", "/path")
        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_notebook_sql_has_on_conflict(self, repo, mock_conn, user_id, target_date):
        await repo.save_notebook(user_id, "nb-abc", "Titulo", target_date, "conteudo", "/path")
        sql = mock_conn.execute.call_args.args[0]
        assert "ON CONFLICT" in sql

    # ── Constructor ───────────────────────────────────────────────────────

    def test_stores_connection(self, mock_conn):
        repo = NotebookRepository(mock_conn)
        assert repo.conn is mock_conn
