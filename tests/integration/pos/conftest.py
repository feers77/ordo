"""Entorno de la tienda de ropa: comercial completo mas caja y sala de ventas."""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.commercial import build_retail_shop

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def pos_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_pos_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (make up)")
    yield _admin_dsn().rsplit("/", 1)[0] + f"/{db_name}"
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture
async def shop(pos_db_url: str) -> AsyncIterator[dict[str, Any]]:
    tenant = f"pos{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(pos_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_retail_shop(session, tenant, modules_root=MODULES_ROOT)
    yield data
    await session.close()
    await engine.dispose()


async def stock_in(shop: dict[str, Any], quantity: str, cost: str) -> None:
    """Carga la sala de ventas desde el proveedor, a un costo dado.

    Desde F12-02c validar un ticket mueve stock, así que vender exige haber
    recibido antes: es la vida real de una tienda.
    """
    from decimal import Decimal

    from modules.stock.services import StockService

    service = StockService(shop["env"])
    picking_id = await service.create_picking(
        picking_type="in",
        date="2026-08-04",
        company_id=shop["company_id"],
        partner_id=shop["vendor_id"],
        origin="Carga tienda",
        moves=[
            {
                "product_id": shop["product_id"],
                "quantity": quantity,
                "location_from_id": shop["loc_supplier"],
                "location_to_id": shop["loc_store"],
                "price_unit": Decimal(cost),
            }
        ],
    )
    await service.action_validate(picking_id)
