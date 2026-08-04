"""Tests del schema semántico que consumen los agentes (F2, PLAN §3.6)."""

import pytest
from ordo_core.errors import KernelError
from ordo_core.fields import Char, Many2one, Monetary, Selection
from ordo_core.model import Model
from ordo_core.registry import Module, Registry
from ordo_core.semantic import build_schema


def demo_registry() -> Registry:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(required=True, agent_hint="Nombre del contacto", examples=["ACME SpA"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(required=True, agent_hint="Número del documento", examples=["SO0001"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["42"])
        amount_total = Monetary(agent_hint="Total con impuestos", examples=["11305.00"])
        state = Selection(
            [("draft", "Borrador"), ("sale", "Confirmada")],
            agent_hint="Estado del ciclo de vida",
            examples=["draft"],
        )

    return Registry.build([Module("demo", models=[Partner, SaleOrder])])


class TestSemanticSchema:
    def test_includes_all_models(self) -> None:
        schema = build_schema(demo_registry())
        assert {m["model"] for m in schema["models"]} == {"res.partner", "sale.order"}

    def test_can_filter_by_model(self) -> None:
        schema = build_schema(demo_registry(), models=["sale.order"])
        assert len(schema["models"]) == 1

    def test_unknown_model_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            build_schema(demo_registry(), models=["no.existe"])
        assert exc.value.code == "MODEL_NOT_FOUND"

    def test_every_business_field_has_hint_and_examples(self) -> None:
        schema = build_schema(demo_registry())
        for model in schema["models"]:
            for name, field in model["fields"].items():
                assert field["hint"], f"{model['model']}.{name} sin hint"
                assert field.get("examples"), f"{model['model']}.{name} sin examples"

    def test_compact_hides_technical_fields(self) -> None:
        schema = build_schema(demo_registry(), compact=True)
        sale = next(m for m in schema["models"] if m["model"] == "sale.order")
        assert "create_uid" not in sale["fields"]
        assert "name" in sale["fields"]

    def test_full_includes_technical_fields(self) -> None:
        schema = build_schema(demo_registry(), compact=False)
        sale = next(m for m in schema["models"] if m["model"] == "sale.order")
        assert "create_uid" in sale["fields"]

    def test_relations_and_selection_values_exposed(self) -> None:
        schema = build_schema(demo_registry())
        sale = next(m for m in schema["models"] if m["model"] == "sale.order")
        assert sale["fields"]["partner_id"]["relates_to"] == "res.partner"
        assert sale["fields"]["state"]["values"] == ["draft", "sale"]

    def test_conventions_documented_for_agents(self) -> None:
        schema = build_schema(demo_registry())
        assert "decimales" in schema["conventions"]["money"]
        assert "dry_run" in schema["conventions"]["writes"]
        assert schema["operators"]
        assert "prefija" in schema["domain_syntax"]
