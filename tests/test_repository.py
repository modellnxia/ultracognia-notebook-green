"""
Unit tests for app/repositories/conversations.py
Coverage goal: 100%
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.conversations import ConversationMessageRepository


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
    async def test_returns_empty_list_when_no_rows(
        self, repo, mock_conn, user_id, target_date
    ):
        mock_conn.fetch.return_value = []
        result = await repo.fetch_messages_by_user_and_date(user_id, target_date)
        assert result == []

    @pytest.mark.asyncio
    async def test_calls_fetch_with_correct_args(
        self, repo, mock_conn, user_id, target_date
    ):
        mock_conn.fetch.return_value = []
        await repo.fetch_messages_by_user_and_date(user_id, target_date)
        args = mock_conn.fetch.call_args
        # first positional arg is the SQL string; $1 and $2 are user_id and target_date
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

    # ── Constructor ───────────────────────────────────────────────────────

    def test_stores_connection(self, mock_conn):
        repo = ConversationMessageRepository(mock_conn)
        assert repo.conn is mock_conn
