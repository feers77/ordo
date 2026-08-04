"""E2E del sistema de módulos: instalar `base` y operar con sus modelos.

Comprueba el camino completo que seguirá cada módulo nativo: descubrirlo en
disco, construir el registro, crear sus tablas en un tenant nuevo, registrar
la versión instalada y usar los modelos a través del ORM.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest
from ordo_core import Environment
from ordo_core.installer import ModuleInstaller
from ordo_core.modules import ModuleLoader
from ordo_core.recordset import RecordSet
from ordo_core.registry import Registry
from ordo_core.semantic import build_schema
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.e2e

MODULES_ROOT = Path(__file__).resolve().parents[2] / "modules"


@pytest.fixture
async def env(e2e_db_url: str) -> AsyncIterator[Environment]:
    tenant = f"m{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(e2e_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "t_{tenant}"'))
    await session.commit()
    registry = Registry.build(ModuleLoader([MODULES_ROOT]).load())
    environment = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await environment.bind()
    yield environment
    await session.close()
    await engine.dispose()


class TestModuleInstallEndToEnd:
    async def test_install_and_operate(self, env: Environment) -> None:
        loader = ModuleLoader([MODULES_ROOT])
        loader.load()  # descubre qué modelos aporta cada módulo
        manifest = loader.discover()["base"]
        installer = ModuleInstaller(env.session, env.registry, loader.models_by_module)

        # 1. Instalar el módulo crea las tablas de todos sus modelos
        result = await installer.install(manifest)
        assert result["module"] == "base"
        assert (await installer.installed())["base"] == manifest.version
        await env.session.commit()

        # 2. Un agente descubre los modelos por el schema semántico
        schema = build_schema(env.registry, models=["res.partner", "res.company"])
        partner_schema = next(m for m in schema["models"] if m["model"] == "res.partner")
        assert partner_schema["fields"]["vat"]["hint"]
        assert partner_schema["fields"]["company_id"]["relates_to"] == "res.company"

        # 3. Crear la moneda y la compañía, en ese orden por la dependencia
        currencies = RecordSet(env, "res.currency")
        [clp_id] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
        companies = RecordSet(env, "res.company")
        [company_id] = await companies.create(
            [
                {
                    "name": "ACME SpA",
                    "vat": "76.123.456-7",
                    "currency_id": clp_id,
                    "country_code": "CL",
                }
            ]
        )

        # 4. Contactos: una empresa y una persona que le pertenece
        partners = RecordSet(env, "res.partner")
        [customer_id] = await partners.create(
            [
                {
                    "name": "Cliente Uno SpA",
                    "is_company": True,
                    "vat": "77.987.654-3",
                    "email": "contacto@clienteuno.cl",
                    "customer_rank": 1.0,
                    "company_id": company_id,
                }
            ]
        )
        [contact_id] = await partners.create(
            [
                {
                    "name": "María Pérez",
                    "is_company": False,
                    "parent_id": customer_id,
                    "email": "maria@clienteuno.cl",
                    "company_id": company_id,
                }
            ]
        )
        await env.session.commit()

        # 5. Buscar por ruta punteada: contactos de empresas que son clientes
        found = await partners.search(
            [("parent_id.name", "=", "Cliente Uno SpA")], fields=["name", "email"]
        )
        assert [row["name"] for row in found["rows"]] == ["María Pérez"]

        # 6. Unidades de medida: la conversión vive dentro de una categoría
        categories = RecordSet(env, "uom.category")
        [weight_id] = await categories.create([{"name": "Peso"}])
        uoms = RecordSet(env, "uom.uom")
        await uoms.create(
            [
                {
                    "name": "Kilogramo",
                    "category_id": weight_id,
                    "factor": 1.0,
                    "uom_type": "reference",
                },
                {
                    "name": "Gramo",
                    "category_id": weight_id,
                    "factor": 1000.0,
                    "uom_type": "smaller",
                },
            ]
        )
        await env.session.commit()
        weight_units = await uoms.search([("category_id", "=", weight_id)], fields=["name"])
        assert len(weight_units["rows"]) == 2

        # 7. Tasa de cambio fechada: convertir usa la vigente al documento
        rates = RecordSet(env, "res.currency.rate")
        [usd_id] = await currencies.create(
            [{"name": "USD", "symbol": "US$", "decimal_places": "2"}]
        )
        await rates.create(
            [
                {
                    "currency_id": usd_id,
                    "company_id": company_id,
                    "date_from": "2026-01-01",
                    "rate": 900.0,
                },
                {
                    "currency_id": usd_id,
                    "company_id": company_id,
                    "date_from": "2026-08-01",
                    "rate": 950.5,
                },
            ]
        )
        await env.session.commit()
        vigentes = await rates.search(
            [("currency_id", "=", usd_id), ("date_from", "<=", "2026-08-04")],
            fields=["rate", "date_from"],
        )
        assert len(vigentes["rows"]) == 2  # ambas históricas; la app elige la última

        # 8. El contacto sigue ahí, con su compañía
        [row] = await partners.read([contact_id], fields=["name", "company_id"])
        assert row["name"] == "María Pérez"
        assert row["company_id"] == company_id

    async def test_reinstall_is_idempotent(self, env: Environment) -> None:
        loader = ModuleLoader([MODULES_ROOT])
        loader.load()
        manifest = loader.discover()["base"]
        installer = ModuleInstaller(env.session, env.registry, loader.models_by_module)

        await installer.install(manifest)
        await installer.install(manifest)  # no debe fallar ni duplicar
        installed = await installer.installed()
        assert installed["base"] == manifest.version
        count = await env.session.scalar(text("SELECT count(*) FROM ir_module"))
        assert count == 1
        await env.session.commit()

    async def test_monetary_still_rejects_float(self, env: Environment) -> None:
        """La regla del dinero se sostiene también en los módulos."""
        from ordo_core.errors import KernelError

        loader = ModuleLoader([MODULES_ROOT])
        loader.load()
        installer = ModuleInstaller(env.session, env.registry, loader.models_by_module)
        await installer.install(loader.discover()["base"])
        await env.session.commit()

        # res.company no tiene campos monetarios todavía; se valida el mecanismo
        # con el propio compilador: un Decimal se acepta y un float se rechaza.
        from ordo_core.fields import Monetary

        with pytest.raises(KernelError) as exc:
            Monetary(default=1.5, agent_hint="x", examples=["1.50"])  # type: ignore[arg-type]
        assert exc.value.code == "FIELD_INVALID_DEFINITION"
        assert Monetary(default=Decimal("0"), agent_hint="x", examples=["0"]).default == 0
