"""Tests del catálogo con variantes (diseño F12-01, ADR-018).

Lo que se protege aquí no es la sintaxis de los modelos —el registry ya la
valida al construirse— sino tres decisiones que un refactor podría deshacer sin
darse cuenta: que la variante sigue siendo `product.product`, que un producto
plano sigue siendo válido, y que la pertenencia de la variante se puede filtrar
en SQL.
"""

from pathlib import Path

import pytest
from ordo_core.fields import TECHNICAL_FIELDS
from ordo_core.installer import table_ddl
from ordo_core.modules import ModuleLoader
from ordo_core.registry import Registry
from ordo_core.semantic import build_schema

MODULES_ROOT = Path(__file__).resolve().parents[2]

CATALOG_MODELS = {
    "product.product",
    "product.template",
    "product.category",
    "product.attribute",
    "product.attribute.value",
    "product.template.attribute.line",
    "product.variant.value",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.build(ModuleLoader([MODULES_ROOT]).load())


class TestModuleLoads:
    def test_all_models_registered(self, registry: Registry) -> None:
        assert set(registry.model_names) >= CATALOG_MODELS

    def test_manifest_declares_the_catalog_growth(self) -> None:
        """La versión sube: es el rastro en ir_module que hace upgradable el tenant."""
        manifests = ModuleLoader([MODULES_ROOT]).discover()
        assert manifests["product"].version == "0.2.0"
        assert manifests["product"].depends == ["base", "account"]

    def test_tables_can_be_generated(self, registry: Registry) -> None:
        for model in CATALOG_MODELS:
            statements = table_ddl(registry[model])
            assert statements[0].startswith("CREATE TABLE IF NOT EXISTS")


class TestAgentReadability:
    def test_every_business_field_documents_itself(self, registry: Registry) -> None:
        for model in CATALOG_MODELS:
            for name, field in registry[model].fields.items():
                if name in TECHNICAL_FIELDS:
                    continue
                assert field.agent_hint, f"{model}.{name} sin agent_hint"
                assert field.examples, f"{model}.{name} sin examples"

    def test_hints_are_explanations_not_labels(self, registry: Registry) -> None:
        for model in CATALOG_MODELS:
            for name, field in registry[model].fields.items():
                if name in TECHNICAL_FIELDS or not field.agent_hint:
                    continue
                assert len(field.agent_hint) > len(name) + 5, (
                    f"{model}.{name}: el hint '{field.agent_hint}' no explica nada"
                )

    def test_schema_tells_an_agent_where_the_variant_hangs_from(self, registry: Registry) -> None:
        schema = build_schema(registry, models=["product.product"])
        fields = schema["models"][0]["fields"]
        assert fields["template_id"]["relates_to"] == "product.template"
        assert "sin variantes" in fields["template_id"]["hint"]


class TestVariantAnchoring:
    """ADR-018: la variante es product.product; el template solo agrupa."""

    def test_the_variant_is_still_the_product(self, registry: Registry) -> None:
        variant_value = registry["product.variant.value"]
        assert variant_value.fields["product_id"].comodel == "product.product"

    def test_a_flat_product_remains_valid(self, registry: Registry) -> None:
        """template_id nullable: los productos sin variantes y los servicios
        siguen funcionando exactamente igual que antes."""
        template_id = registry["product.product"].fields["template_id"]
        assert not template_id.required

    def test_variant_membership_is_queryable_in_sql(self, registry: Registry) -> None:
        """El valor de la variante vive en un modelo con índices, no en un
        blob: "qué queda en talla M" tiene que resolverse en la base."""
        fields = registry["product.variant.value"].fields
        for name in ("product_id", "attribute_id", "value_id"):
            assert fields[name].store, f"{name} no almacenado: no se puede filtrar"
            assert fields[name].index, f"{name} sin índice: filtrar sería un scan"

    def test_matrix_axis_is_configuration_not_a_query_surface(self, registry: Registry) -> None:
        """El eje se lee entero siempre, así que va como Char de ids. Si alguien
        lo convierte en algo que parezca filtrable, este test lo detiene: el
        kernel no tiene Many2many almacenado y el filtro sería una mentira."""
        value_ids = registry["product.template.attribute.line"].fields["value_ids"]
        assert value_ids.field_type == "char"
        assert value_ids.required


class TestSemantics:
    def test_display_type_offers_the_retail_axes(self, registry: Registry) -> None:
        allowed = registry["product.attribute"].fields["display_type"].allowed_values
        assert {"size", "color"} <= set(allowed)

    def test_category_is_a_tree(self, registry: Registry) -> None:
        assert registry["product.category"].fields["parent_id"].comodel == "product.category"

    def test_template_carries_the_defaults_variants_copy(self, registry: Registry) -> None:
        """Lo que la variante copia al generarse tiene que existir en el template."""
        template = registry["product.template"].fields
        for name in ("product_type", "list_price", "tracking", "uom_id", "category_id"):
            assert name in template, f"el template no puede aportar {name}"
