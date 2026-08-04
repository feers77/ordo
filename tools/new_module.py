"""Scaffolding de un módulo nativo (F2-07).

    python tools/new_module.py ventas [--path modules] [--depends base,partner]

Genera un módulo que ya carga, ya pasa sus tests y ya cumple las reglas del
proyecto: campos con agent_hint y examples, y un test que lo verifica.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

MANIFEST = """name: {name}
version: 0.1.0
summary: {title}
depends: [{depends}]
category: Sin categoría
"""

MODELS = '''"""Modelos del módulo {name}."""

from ordo_core.fields import Char, Selection
from ordo_core.model import Model


class {klass}(Model):
    _name = "{name}.item"
    _description = "{title}"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre visible del registro",
        examples=["Ejemplo 1"],
    )
    state = Selection(
        [("draft", "Borrador"), ("done", "Listo")],
        default="draft",
        agent_hint="Estado del ciclo de vida del registro",
        examples=["draft"],
    )
'''

TEST = '''"""Tests del módulo {name}."""

from pathlib import Path

from ordo_core.modules import ModuleLoader
from ordo_core.registry import Registry

MODULE_ROOT = Path(__file__).resolve().parents[2]


def test_module_loads() -> None:
    registry = Registry.build(ModuleLoader([MODULE_ROOT]).load())
    assert "{name}.item" in registry.model_names


def test_business_fields_document_themselves() -> None:
    """Sin agent_hint y examples un agente no puede usar el modelo."""
    registry = Registry.build(ModuleLoader([MODULE_ROOT]).load())
    item = registry["{name}.item"]
    for field_name in ("name", "state"):
        field = item.fields[field_name]
        assert field.agent_hint
        assert field.examples
'''

INIT = '''"""Módulo {name}."""
'''

README = """# Módulo `{name}`

{title}

## Qué incluye

- `models.py` — modelos del módulo. Todo campo de negocio lleva `agent_hint` y
  `examples`: sin eso el registro falla al construirse.
- `migrations/` — solo para lo que el generador de tablas no puede inferir
  (renombres, backfills, índices especiales). Las tablas de los modelos las crea
  el kernel desde el registro.
- `tests/` — corren con `uv run pytest modules/{name}`.

## Dependencias

`{depends}`

Un módulo solo puede extender modelos de los módulos que declara aquí.
"""


def create_module(name: str, root: Path, depends: list[str]) -> Path:
    if not NAME_RE.match(name):
        msg = f"Nombre de módulo inválido: {name!r}. Usa minúsculas, dígitos y guion bajo."
        raise SystemExit(msg)
    directory = root / name
    if directory.exists():
        msg = f"El módulo ya existe: {directory}"
        raise SystemExit(msg)

    title = name.replace("_", " ").capitalize()
    klass = "".join(part.capitalize() for part in name.split("_")) + "Item"
    depends_str = ", ".join(depends)

    (directory / "migrations").mkdir(parents=True)
    (directory / "tests").mkdir()
    (directory / "manifest.yaml").write_text(
        MANIFEST.format(name=name, title=title, depends=depends_str)
    )
    (directory / "__init__.py").write_text(INIT.format(name=name))
    (directory / "models.py").write_text(MODELS.format(name=name, title=title, klass=klass))
    (directory / "README.md").write_text(
        README.format(name=name, title=title, depends=depends_str or "ninguna")
    )
    (directory / "tests" / "__init__.py").write_text("")
    (directory / "tests" / f"test_{name}.py").write_text(TEST.format(name=name))
    (directory / "migrations" / ".gitkeep").write_text("")
    return directory


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el esqueleto de un módulo ORDO")
    parser.add_argument("name", help="nombre del módulo (minúsculas)")
    parser.add_argument("--path", default="modules", help="directorio raíz de módulos")
    parser.add_argument("--depends", default="", help="dependencias separadas por coma")
    args = parser.parse_args()

    depends = [d.strip() for d in args.depends.split(",") if d.strip()]
    directory = create_module(args.name, Path(args.path), depends)
    print(f"Módulo creado en {directory}")
    print(f"Pruébalo: uv run pytest {directory}")


if __name__ == "__main__":
    main()
