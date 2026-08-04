"""Seguridad declarativa: los YAML de los módulos cubren todo lo que existe."""

from pathlib import Path

import pytest
from ordo_core.modules import ModuleLoader
from ordo_core.registry import Registry
from ordo_core.security import SecurityError, _parse_file, load_security_specs

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


class TestLoader:
    def test_specs_merge_across_modules(self) -> None:
        specs = {spec.name: spec for spec in load_security_specs(MODULES_ROOT)}
        assert {"ventas", "compras", "contabilidad", "tesoreria", "auditor"} <= set(specs)
        # ventas junta grants de base, account, sale y einvoicing
        ventas = specs["ventas"].grants
        assert "read" in ventas["res.partner"]
        assert "create" in ventas["sale.order"]
        assert ventas["account.move"] == frozenset({"read"})

    def test_every_model_is_consciously_granted(self) -> None:
        """Denegación por defecto consciente: ningún modelo queda sin dueño.

        Si un módulo agrega un modelo y nadie decide quién lo toca, este test
        rompe: la omisión deja de ser silenciosa.
        """
        registry = Registry.build(ModuleLoader([MODULES_ROOT]).load())
        granted = {model for spec in load_security_specs(MODULES_ROOT) for model in spec.grants}
        missing = set(registry.model_names) - granted
        assert not missing, f"Modelos sin entrada en ningún security.yaml: {sorted(missing)}"

    def test_nobody_can_unlink_posted_history(self) -> None:
        """Asientos y documentos electrónicos: sin unlink para nadie."""
        for spec in load_security_specs(MODULES_ROOT):
            for model in ("account.move", "account.move.line", "edi.document"):
                perms = spec.grants.get(model, frozenset())
                assert "unlink" not in perms, f"{spec.name} puede borrar {model}"

    def test_invalid_perm_is_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "security.yaml"
        bad.write_text("roles:\n  x:\n    sale.order: [read, fly]\n")
        with pytest.raises(SecurityError) as excinfo:
            _parse_file(bad)
        assert excinfo.value.code == "SECURITY_INVALID_PERM"

    def test_shapeless_yaml_is_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "security.yaml"
        bad.write_text("- no es un mapa\n")
        with pytest.raises(SecurityError) as excinfo:
            _parse_file(bad)
        assert excinfo.value.code == "SECURITY_INVALID_SHAPE"
