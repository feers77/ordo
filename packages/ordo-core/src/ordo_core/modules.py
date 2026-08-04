"""Native module system (design F2-07).

A module declares itself in `manifest.yaml`, ships its models and its own
migrations. The loader validates the dependency graph at startup, so a
missing or circular dependency fails the boot instead of surfacing later
as mysterious ordering behaviour.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ordo_core.errors import KernelError
from ordo_core.model import Model
from ordo_core.registry import Module

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    summary: str
    depends: list[str] = field(default_factory=list)
    category: str = "Sin categoría"
    path: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> Manifest:
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise KernelError(
                "MODULE_MANIFEST_INVALID", f"{path}: manifiesto YAML inválido"
            ) from exc
        if not isinstance(raw, dict):
            raise KernelError("MODULE_MANIFEST_INVALID", f"{path}: el manifiesto debe ser un mapa")

        name = str(raw.get("name", ""))
        if not NAME_RE.match(name):
            raise KernelError(
                "MODULE_MANIFEST_INVALID",
                f"{path}: nombre de módulo inválido: {name!r}",
                hint="Minúsculas, dígitos y guion bajo; debe empezar con letra.",
            )
        version = str(raw.get("version", ""))
        if not VERSION_RE.match(version):
            raise KernelError(
                "MODULE_MANIFEST_INVALID",
                f"{path}: versión inválida: {version!r}",
                hint="Usa semver: MAJOR.MINOR.PATCH.",
            )
        depends = raw.get("depends") or []
        if not isinstance(depends, list) or any(not isinstance(d, str) for d in depends):
            raise KernelError(
                "MODULE_MANIFEST_INVALID", f"{path}: 'depends' debe ser una lista de nombres"
            )
        return cls(
            name=name,
            version=version,
            summary=str(raw.get("summary", name)),
            depends=list(depends),
            category=str(raw.get("category", "Sin categoría")),
            path=path.parent,
        )


class ModuleLoader:
    """Discovers modules on disk and turns them into registry modules."""

    def __init__(self, search_paths: list[Path]) -> None:
        self.search_paths = search_paths
        # Modelos que define cada módulo. El instalador lo usa para no obligar
        # a mantener esa lista a mano, que es una fuente segura de olvidos.
        self.models_by_module: dict[str, list[str]] = {}

    def discover(self) -> dict[str, Manifest]:
        manifests: dict[str, Manifest] = {}
        for root in self.search_paths:
            if not root.exists():
                continue
            for manifest_path in sorted(root.glob("*/manifest.yaml")):
                manifest = Manifest.from_file(manifest_path)
                if manifest.name in manifests:
                    raise KernelError(
                        "MODULE_DUPLICATE",
                        f"El módulo '{manifest.name}' está definido dos veces",
                    )
                manifests[manifest.name] = manifest
        return manifests

    def validate_graph(self, manifests: dict[str, Manifest]) -> list[str]:
        """Topological order; missing deps and cycles fail here, not at runtime."""
        for manifest in manifests.values():
            for dependency in manifest.depends:
                if dependency not in manifests:
                    raise KernelError(
                        "MODULE_MISSING_DEPENDENCY",
                        f"El módulo '{manifest.name}' depende de '{dependency}', "
                        "que no está disponible",
                    )
        order: list[str] = []
        state: dict[str, int] = {}

        def visit(name: str, path: tuple[str, ...]) -> None:
            status = state.get(name, 0)
            if status == 1:
                raise KernelError(
                    "MODULE_DEPENDENCY_CYCLE",
                    f"Ciclo de dependencias: {' -> '.join([*path, name])}",
                )
            if status == 2:
                return
            state[name] = 1
            for dependency in sorted(manifests[name].depends):
                visit(dependency, (*path, name))
            state[name] = 2
            order.append(name)

        for name in sorted(manifests):
            visit(name, ())
        return order

    def load(self) -> list[Module]:
        manifests = self.discover()
        order = self.validate_graph(manifests)
        modules: list[Module] = []
        defined_by: dict[str, str] = {}

        for name in order:
            manifest = manifests[name]
            models = self._import_models(manifest)
            for model_cls in models:
                if model_cls._name:
                    owner = defined_by.get(model_cls._name)
                    if owner is not None:
                        raise KernelError(
                            "MODULE_MODEL_CONFLICT",
                            f"'{name}' define el modelo '{model_cls._name}', que ya "
                            f"definió '{owner}'",
                            hint="Para ampliar un modelo existente usa _inherit.",
                        )
                    defined_by[model_cls._name] = name
                elif model_cls._inherit:
                    owner = defined_by.get(model_cls._inherit)
                    if owner is not None and owner != name and owner not in manifest.depends:
                        raise KernelError(
                            "MODULE_UNDECLARED_DEPENDENCY",
                            f"'{name}' extiende '{model_cls._inherit}', definido por "
                            f"'{owner}', sin declararlo en depends",
                            hint="Agrega la dependencia al manifiesto.",
                        )
            self.models_by_module[name] = [m._name for m in models if m._name]
            self._import_extra(manifest, "actions.py")
            self._import_extra(manifest, "reports.py")
            modules.append(Module(name=name, models=models, depends=list(manifest.depends)))
        return modules

    def _import_extra(self, manifest: Manifest, filename: str) -> None:
        """Imports an optional module file (actions.py, reports.py) if present."""
        assert manifest.path is not None
        extra_file = manifest.path / filename
        if not extra_file.exists():
            return
        module_name = f"ordo_modules.{manifest.name}.{extra_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, extra_file)
        if spec is None or spec.loader is None:
            raise KernelError("MODULE_IMPORT_FAILED", f"No se pudo importar {extra_file}")
        imported = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = imported
        spec.loader.exec_module(imported)

    def _import_models(self, manifest: Manifest) -> list[type[Model]]:
        assert manifest.path is not None
        models_file = manifest.path / "models.py"
        if not models_file.exists():
            return []
        module_name = f"ordo_modules.{manifest.name}.models"
        spec = importlib.util.spec_from_file_location(module_name, models_file)
        if spec is None or spec.loader is None:
            raise KernelError("MODULE_IMPORT_FAILED", f"No se pudo importar {models_file}")
        imported = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = imported
        spec.loader.exec_module(imported)
        return [
            obj
            for obj in vars(imported).values()
            if isinstance(obj, type) and issubclass(obj, Model) and obj is not Model
        ]


def migration_files(manifest: Manifest) -> list[Path]:
    """Migrations of a module, in lexicographic order (001_, 002_, ...)."""
    assert manifest.path is not None
    directory = manifest.path / "migrations"
    if not directory.exists():
        return []
    return sorted(directory.glob("*.sql"))


def manifest_summary(manifests: dict[str, Manifest]) -> list[dict[str, Any]]:
    return [
        {
            "name": m.name,
            "version": m.version,
            "summary": m.summary,
            "depends": m.depends,
            "category": m.category,
        }
        for m in sorted(manifests.values(), key=lambda m: m.name)
    ]
