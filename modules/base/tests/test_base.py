"""Tests del módulo base.

Validan dos cosas: que el módulo carga bien, y que sus modelos cumplen las
reglas que hacen que un agente pueda usarlos sin documentación aparte.
"""

from pathlib import Path

import pytest
from ordo_core.fields import TECHNICAL_FIELDS
from ordo_core.installer import table_ddl
from ordo_core.modules import ModuleLoader
from ordo_core.registry import Registry
from ordo_core.semantic import build_schema

MODULES_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_MODELS = {
    "res.company",
    "res.currency",
    "res.currency.rate",
    "res.partner",
    "uom.category",
    "uom.uom",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.build(ModuleLoader([MODULES_ROOT]).load())


class TestModuleLoads:
    def test_all_models_registered(self, registry: Registry) -> None:
        assert set(registry.model_names) >= EXPECTED_MODELS

    def test_manifest_is_valid(self) -> None:
        manifests = ModuleLoader([MODULES_ROOT]).discover()
        assert manifests["base"].version
        assert manifests["base"].depends == []

    def test_tables_can_be_generated(self, registry: Registry) -> None:
        for model in EXPECTED_MODELS:
            statements = table_ddl(registry[model])
            assert statements[0].startswith("CREATE TABLE IF NOT EXISTS")


class TestAgentReadability:
    def test_every_business_field_documents_itself(self, registry: Registry) -> None:
        """Sin hint y ejemplos, un agente no puede decidir qué escribir."""
        for model in EXPECTED_MODELS:
            for name, field in registry[model].fields.items():
                if name in TECHNICAL_FIELDS:
                    continue
                assert field.agent_hint, f"{model}.{name} sin agent_hint"
                assert field.examples, f"{model}.{name} sin examples"

    def test_hints_are_explanations_not_labels(self, registry: Registry) -> None:
        """Un hint que solo repite el nombre del campo no aporta nada."""
        for model in EXPECTED_MODELS:
            for name, field in registry[model].fields.items():
                if name in TECHNICAL_FIELDS or not field.agent_hint:
                    continue
                assert len(field.agent_hint) > len(name) + 5, (
                    f"{model}.{name}: el hint '{field.agent_hint}' no explica nada"
                )

    def test_semantic_schema_exposes_relations(self, registry: Registry) -> None:
        schema = build_schema(registry, models=["res.partner"])
        partner = schema["models"][0]
        assert partner["fields"]["company_id"]["relates_to"] == "res.company"
        assert partner["fields"]["is_company"]["hint"]


class TestModelSemantics:
    def test_currency_declares_decimal_places(self, registry: Registry) -> None:
        """El redondeo por moneda es fuente clásica de descuadres."""
        field = registry["res.currency"].fields["decimal_places"]
        assert field.allowed_values == {"0", "2", "4"}  # type: ignore[attr-defined]

    def test_rates_are_dated(self, registry: Registry) -> None:
        """Convertir con la tasa de hoy un documento de ayer da un importe falso."""
        assert "date_from" in registry["res.currency.rate"].fields
        assert registry["res.currency.rate"].fields["date_from"].required

    def test_uom_conversion_is_scoped_by_category(self, registry: Registry) -> None:
        """Solo se convierten unidades de la misma magnitud."""
        uom = registry["uom.uom"]
        assert uom.fields["category_id"].required
        assert uom.fields["category_id"].comodel == "uom.category"  # type: ignore[attr-defined]

    def test_partner_can_be_person_or_company(self, registry: Registry) -> None:
        partner = registry["res.partner"]
        assert partner.fields["is_company"].field_type == "boolean"
        assert partner.fields["parent_id"].comodel == "res.partner"  # type: ignore[attr-defined]

    def test_company_has_accounting_currency(self, registry: Registry) -> None:
        company = registry["res.company"]
        assert company.fields["currency_id"].required
        assert company.fields["currency_id"].comodel == "res.currency"  # type: ignore[attr-defined]
