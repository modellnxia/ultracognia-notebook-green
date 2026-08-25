"""Testes de app/decks/theme_repository.py — mesmo padrão de mock do resto do projeto."""

import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.decks.theme_repository import ThemeRepository


class TestCreateTheme:
    @pytest.mark.asyncio
    async def test_inserts_theme_with_palette_serialized_as_json(self):
        conn = AsyncMock()
        theme_id = uuid.uuid4()
        conn.fetchrow.return_value = {"id": theme_id, "name": "padrao"}
        repo = ThemeRepository(conn)

        await repo.create_theme(
            "padrao", {"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"}, "Inter, sans-serif"
        )

        args = conn.fetchrow.call_args.args
        sql = args[0]
        assert "INSERT INTO deck_themes" in sql
        assert "ON CONFLICT (client_id, name) DO UPDATE" in sql
        # palette vai serializado como texto (json.dumps), não dict cru — mesmo
        # padrão de deck_job_steps.output_ref (sem codec jsonb no asyncpg deste projeto)
        assert json.loads(args[3]) == {"primary": "#3B82F6", "background": "#0D1B2E", "text": "#F2F2F2"}

    @pytest.mark.asyncio
    async def test_client_id_and_logo_url_default_to_none(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {"id": uuid.uuid4()}
        repo = ThemeRepository(conn)

        await repo.create_theme("padrao", {"primary": "#fff"}, "Inter")

        args = conn.fetchrow.call_args.args
        assert args[1] is None  # client_id
        assert args[5] is None  # logo_url

    @pytest.mark.asyncio
    async def test_passes_client_id_and_logo_url_through(self):
        conn = AsyncMock()
        client_id = uuid.uuid4()
        conn.fetchrow.return_value = {"id": uuid.uuid4()}
        repo = ThemeRepository(conn)

        await repo.create_theme(
            "padrao", {"primary": "#fff"}, "Inter", client_id=client_id, logo_url="https://x/logo.png"
        )

        args = conn.fetchrow.call_args.args
        assert args[1] == client_id
        assert args[5] == "https://x/logo.png"


class TestGetTheme:
    @pytest.mark.asyncio
    async def test_returns_row_when_found(self):
        conn = AsyncMock()
        theme_id = uuid.uuid4()
        conn.fetchrow.return_value = {"id": theme_id}
        repo = ThemeRepository(conn)

        row = await repo.get_theme(theme_id)

        assert row["id"] == theme_id
        conn.fetchrow.assert_awaited_once_with("SELECT * FROM deck_themes WHERE id = $1", theme_id)

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = None
        repo = ThemeRepository(conn)

        assert await repo.get_theme(uuid.uuid4()) is None


class TestListThemes:
    @pytest.mark.asyncio
    async def test_lists_all_when_no_client_id(self):
        conn = AsyncMock()
        conn.fetch.return_value = []
        repo = ThemeRepository(conn)

        await repo.list_themes()

        sql, *args = conn.fetch.call_args.args
        assert args == []
        assert "WHERE" not in sql

    @pytest.mark.asyncio
    async def test_filters_by_client_id_when_given(self):
        conn = AsyncMock()
        client_id = uuid.uuid4()
        conn.fetch.return_value = []
        repo = ThemeRepository(conn)

        await repo.list_themes(client_id)

        sql, *args = conn.fetch.call_args.args
        assert args == [client_id]
        assert "WHERE client_id = $1" in sql
