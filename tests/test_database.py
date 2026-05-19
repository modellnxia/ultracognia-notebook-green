"""
Unit tests for app/core/database.py
Coverage goal: 100%
"""
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.database as db_module
from app.core.database import close_pool, create_pool, get_db_conn


# ─────────────────────────────────────────────────────────────────────────────
# create_pool
# ─────────────────────────────────────────────────────────────────────────────


class TestCreatePool:
    @pytest.mark.asyncio
    async def test_creates_pool_and_sets_global(self):
        mock_pool = AsyncMock()
        with (
            patch("app.core.database.asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)),
            patch("app.core.database.settings") as mock_settings,
        ):
            mock_settings.DATABASE_URL = "postgresql://user:pw@localhost/db"
            db_module._pool = None
            await create_pool()
        assert db_module._pool is mock_pool

    @pytest.mark.asyncio
    async def test_ssl_context_configured(self):
        """SSL context must disable hostname check and cert verification."""
        captured_ssl = {}

        async def fake_create_pool(**kwargs):
            captured_ssl["ctx"] = kwargs.get("ssl")
            return AsyncMock()

        with (
            patch("app.core.database.asyncpg.create_pool", new=fake_create_pool),
            patch("app.core.database.settings") as mock_settings,
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h/d"
            await create_pool()

        ctx = captured_ssl["ctx"]
        assert ctx is not None
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


# ─────────────────────────────────────────────────────────────────────────────
# close_pool
# ─────────────────────────────────────────────────────────────────────────────


class TestClosePool:
    @pytest.mark.asyncio
    async def test_closes_pool_when_initialized(self):
        mock_pool = AsyncMock()
        db_module._pool = mock_pool
        await close_pool()
        mock_pool.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_op_when_pool_is_none(self):
        db_module._pool = None
        # Should not raise
        await close_pool()


# ─────────────────────────────────────────────────────────────────────────────
# get_db_conn
# ─────────────────────────────────────────────────────────────────────────────


class TestGetDbConn:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_pool_none(self):
        db_module._pool = None
        with pytest.raises(RuntimeError, match="Pool não inicializado"):
            async for _ in get_db_conn():
                pass

    @pytest.mark.asyncio
    async def test_yields_connection_from_pool(self):
        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        db_module._pool = mock_pool

        results = []
        async for conn in get_db_conn():
            results.append(conn)

        assert results == [mock_conn]

    @pytest.mark.asyncio
    async def test_acquire_called_once(self):
        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        db_module._pool = mock_pool

        async for _ in get_db_conn():
            pass

        mock_pool.acquire.assert_called_once()
