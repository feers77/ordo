"""Programmatic Alembic runner for the IAM database."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

SCRIPT_LOCATION = Path(__file__).parent


def _config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def upgrade_to_head_sync(url: str) -> None:
    command.upgrade(_config(url), "head")


async def upgrade_to_head(url: str) -> None:
    """Run migrations without blocking the event loop."""
    await asyncio.to_thread(upgrade_to_head_sync, url)
