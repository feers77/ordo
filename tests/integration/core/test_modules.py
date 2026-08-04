"""Tests del sistema de módulos nativos (F2-07)."""

from pathlib import Path

import pytest
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.installer import ModuleInstaller, table_ddl
from ordo_core.modules import Manifest, ModuleLoader
from ordo_core.registry import Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def write_module(
    root: Path,
    name: str,
    *,
    depends: list[str] | None = None,
    models: str = "",
    migrations: dict[str, str] | None = None,
    version: str = "1.0.0",
) -> Path:
    directory = root / name
    (directory / "migrations").mkdir(parents=True, exist_ok=True)
    depends_yaml = "[" + ", ".join(depends or []) + "]"
    (directory / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\nsummary: Módulo {name}\ndepends: {depends_yaml}\n"
    )
    if models:
        (directory / "models.py").write_text(models)
    for filename, sql in (migrations or {}).items():
        (directory / "migrations" / filename).write_text(sql)
    return directory


PARTNER_MODEL = """
from ordo_core.fields import Char
from ordo_core.model import Model


class Partner(Model):
    _name = "res.partner"
    _description = "Contacto"

    name = Char(required=True, index=True, agent_hint="Nombre", examples=["ACME"])
"""

SALE_MODEL = """
from ordo_core.fields import Char, Many2one
from ordo_core.model import Model


class SaleOrder(Model):
    _name = "sale.order"
    _description = "Orden de venta"

    name = Char(required=True, agent_hint="Número", examples=["SO0001"])
    partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])
"""

PARTNER_EXTENSION = """
from ordo_core.fields import Char
from ordo_core.model import Model


class PartnerExt(Model):
    _inherit = "res.partner"

    vat = Char(agent_hint="RUT del contacto", examples=["76.123.456-7"])
"""


class TestDiscoveryAndGraph:
    def test_discovers_manifests(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "sale", depends=["base"], models=SALE_MODEL)
        manifests = ModuleLoader([tmp_path]).discover()
        assert set(manifests) == {"base", "sale"}
        assert manifests["sale"].depends == ["base"]

    def test_topological_order(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "sale", depends=["base"], models=SALE_MODEL)
        loader = ModuleLoader([tmp_path])
        assert loader.validate_graph(loader.discover()) == ["base", "sale"]

    def test_missing_dependency_fails_at_load(self, tmp_path: Path) -> None:
        write_module(tmp_path, "sale", depends=["fantasma"], models=SALE_MODEL)
        loader = ModuleLoader([tmp_path])
        with pytest.raises(KernelError) as exc:
            loader.validate_graph(loader.discover())
        assert exc.value.code == "MODULE_MISSING_DEPENDENCY"

    def test_cycle_detected(self, tmp_path: Path) -> None:
        write_module(tmp_path, "a", depends=["b"])
        write_module(tmp_path, "b", depends=["a"])
        loader = ModuleLoader([tmp_path])
        with pytest.raises(KernelError) as exc:
            loader.validate_graph(loader.discover())
        assert exc.value.code == "MODULE_DEPENDENCY_CYCLE"

    def test_invalid_version_rejected(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", version="uno")
        with pytest.raises(KernelError) as exc:
            ModuleLoader([tmp_path]).discover()
        assert exc.value.code == "MODULE_MANIFEST_INVALID"

    def test_invalid_name_rejected(self, tmp_path: Path) -> None:
        directory = tmp_path / "Malo"
        directory.mkdir()
        (directory / "manifest.yaml").write_text("name: Malo\nversion: 1.0.0\nsummary: x\n")
        with pytest.raises(KernelError) as exc:
            ModuleLoader([tmp_path]).discover()
        assert exc.value.code == "MODULE_MANIFEST_INVALID"


class TestModelOwnership:
    def test_two_modules_cannot_define_same_model(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "otro", depends=["base"], models=PARTNER_MODEL)
        with pytest.raises(KernelError) as exc:
            ModuleLoader([tmp_path]).load()
        assert exc.value.code == "MODULE_MODEL_CONFLICT"

    def test_extending_without_declaring_dependency_rejected(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "vat", models=PARTNER_EXTENSION)  # sin depends
        with pytest.raises(KernelError) as exc:
            ModuleLoader([tmp_path]).load()
        assert exc.value.code == "MODULE_UNDECLARED_DEPENDENCY"

    def test_extending_with_declared_dependency_works(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "vat", depends=["base"], models=PARTNER_EXTENSION)
        registry = Registry.build(ModuleLoader([tmp_path]).load())
        assert "vat" in registry["res.partner"].fields


class TestSchemaGeneration:
    def test_ddl_includes_technical_and_declared_columns(self, tmp_path: Path) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        registry = Registry.build(ModuleLoader([tmp_path]).load())
        statements = table_ddl(registry["res.partner"])
        create = statements[0]
        assert '"id" serial PRIMARY KEY' in create
        assert '"name" text NOT NULL' in create
        assert '"version" integer' in create
        assert any("ix_res_partner_name" in s for s in statements)

    async def test_tables_created_from_registry(
        self, core_session: AsyncSession, tmp_path: Path
    ) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        registry = Registry.build(ModuleLoader([tmp_path]).load())
        await core_session.execute(text('CREATE SCHEMA IF NOT EXISTS "t_mod1"'))
        env = Environment(session=core_session, tenant="mod1", registry=registry, app_role=None)
        await env.bind()
        installer = ModuleInstaller(core_session, registry)
        await installer.prepare()
        created = await installer.create_tables()
        assert "res_partner" in created
        exists = await core_session.scalar(
            text("SELECT to_regclass('t_mod1.res_partner') IS NOT NULL")
        )
        assert exists is True


class TestInstallation:
    async def _env(self, session: AsyncSession, tenant: str, registry: Registry) -> Environment:
        await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "t_{tenant}"'))
        env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
        await env.bind()
        return env

    async def test_install_records_version(
        self, core_session: AsyncSession, tmp_path: Path
    ) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL, version="1.2.3")
        loader = ModuleLoader([tmp_path])
        registry = Registry.build(loader.load())
        await self._env(core_session, "mod2", registry)
        installer = ModuleInstaller(core_session, registry)
        await installer.install(loader.discover()["base"], ["res.partner"])
        assert (await installer.installed())["base"] == "1.2.3"

    async def test_migration_applied_once(self, core_session: AsyncSession, tmp_path: Path) -> None:
        write_module(
            tmp_path,
            "base",
            models=PARTNER_MODEL,
            migrations={"001_extra.sql": "ALTER TABLE res_partner ADD COLUMN nota text"},
        )
        loader = ModuleLoader([tmp_path])
        registry = Registry.build(loader.load())
        await self._env(core_session, "mod3", registry)
        installer = ModuleInstaller(core_session, registry)
        manifest = loader.discover()["base"]

        first = await installer.install(manifest, ["res.partner"])
        assert first["migrations"] == ["001_extra.sql"]
        second = await installer.install(manifest, ["res.partner"])
        assert second["migrations"] == []  # no se reaplica

    async def test_failed_migration_leaves_module_uninstalled(
        self, core_session: AsyncSession, tmp_path: Path
    ) -> None:
        write_module(
            tmp_path,
            "base",
            models=PARTNER_MODEL,
            migrations={"001_rota.sql": "ESTO NO ES SQL VALIDO"},
        )
        loader = ModuleLoader([tmp_path])
        registry = Registry.build(loader.load())
        await self._env(core_session, "mod4", registry)
        installer = ModuleInstaller(core_session, registry)
        with pytest.raises(Exception):  # noqa: B017
            await installer.install(loader.discover()["base"], ["res.partner"])
        assert "base" not in await installer.installed()

    async def test_dependent_module_installs_after_its_dependency(
        self, core_session: AsyncSession, tmp_path: Path
    ) -> None:
        write_module(tmp_path, "base", models=PARTNER_MODEL)
        write_module(tmp_path, "sale", depends=["base"], models=SALE_MODEL)
        loader = ModuleLoader([tmp_path])
        registry = Registry.build(loader.load())
        await self._env(core_session, "mod5", registry)
        installer = ModuleInstaller(core_session, registry)
        manifests = loader.discover()
        for name, models in (("base", ["res.partner"]), ("sale", ["sale.order"])):
            await installer.install(manifests[name], models)
        assert set(await installer.installed()) == {"base", "sale"}


class TestScaffolding:
    def test_generated_module_is_valid(self, tmp_path: Path) -> None:
        """El esqueleto que produce `make new-module` debe cargar sin tocar nada."""
        import subprocess

        result = subprocess.run(
            ["python", "tools/new_module.py", "ventas", "--path", str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        manifest = Manifest.from_file(tmp_path / "ventas" / "manifest.yaml")
        assert manifest.name == "ventas"
        registry = Registry.build(ModuleLoader([tmp_path]).load())
        assert any(name.startswith("ventas.") for name in registry.model_names)
