"""Tests del registry de modelos y herencia (F2-01) — antes de implementar."""

from typing import ClassVar

import pytest
from ordo_core.errors import KernelError
from ordo_core.fields import Char, Integer, Many2one
from ordo_core.model import Model
from ordo_core.registry import Module, Registry


def base_module() -> Module:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(required=True, agent_hint="Nombre del contacto", examples=["ACME SpA"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(required=True, agent_hint="Número del documento", examples=["SO0001"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["42"])

    return Module("base", models=[Partner, SaleOrder])


class TestRegistryBuild:
    def test_models_registered(self) -> None:
        registry = Registry.build([base_module()])
        assert set(registry.model_names) == {"res.partner", "sale.order"}
        assert registry["sale.order"].description == "Orden de venta"

    def test_table_name_derived_from_model_name(self) -> None:
        registry = Registry.build([base_module()])
        assert registry["sale.order"].table == "sale_order"

    def test_technical_columns_present(self) -> None:
        registry = Registry.build([base_module()])
        fields = registry["sale.order"].fields
        for name in ("id", "create_uid", "create_date", "write_uid", "write_date", "version"):
            assert name in fields

    def test_registry_is_frozen_after_build(self) -> None:
        registry = Registry.build([base_module()])
        with pytest.raises(KernelError) as exc:
            registry.add_model_definition("x.y", {})
        assert exc.value.code == "REGISTRY_FROZEN"

    def test_unknown_model_raises(self) -> None:
        registry = Registry.build([base_module()])
        with pytest.raises(KernelError) as exc:
            registry["no.existe"]
        assert exc.value.code == "MODEL_NOT_FOUND"


class TestAgentMetadataIsMandatory:
    def test_business_field_without_agent_hint_fails(self) -> None:
        class Bad(Model):
            _name = "bad.model"
            _description = "Malo"

            name = Char(required=True)  # sin agent_hint

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad", models=[Bad])])
        assert exc.value.code == "FIELD_MISSING_AGENT_METADATA"
        assert "bad.model.name" in str(exc.value)

    def test_business_field_without_examples_fails(self) -> None:
        class Bad(Model):
            _name = "bad.model2"
            _description = "Malo"

            name = Char(agent_hint="Nombre")

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad2", models=[Bad])])
        assert exc.value.code == "FIELD_MISSING_AGENT_METADATA"


class TestInheritance:
    def test_inherit_adds_field(self) -> None:
        class SaleOrderExt(Model):
            _inherit = "sale.order"

            note = Char(agent_hint="Nota interna", examples=["Entregar antes del viernes"])

        registry = Registry.build(
            [base_module(), Module("sale_notes", models=[SaleOrderExt], depends=["base"])]
        )
        assert "note" in registry["sale.order"].fields

    def test_inherit_cannot_change_field_type(self) -> None:
        class SaleOrderBad(Model):
            _inherit = "sale.order"

            name = Integer(agent_hint="Número", examples=["1"])

        with pytest.raises(KernelError) as exc:
            Registry.build([base_module(), Module("bad", models=[SaleOrderBad], depends=["base"])])
        assert exc.value.code == "FIELD_TYPE_CONFLICT"

    def test_inherit_of_unknown_model_fails(self) -> None:
        class Orphan(Model):
            _inherit = "no.existe"

            x = Char(agent_hint="x", examples=["y"])

        with pytest.raises(KernelError) as exc:
            Registry.build([base_module(), Module("orphan", models=[Orphan], depends=["base"])])
        assert exc.value.code == "MODEL_NOT_FOUND"

    def test_inherits_delegation_exposes_parent_fields(self) -> None:
        class Template(Model):
            _name = "product.template"
            _description = "Plantilla de producto"

            name = Char(required=True, agent_hint="Nombre del producto", examples=["Café 1kg"])

        class Product(Model):
            _name = "product.product"
            _description = "Variante de producto"
            _inherits: ClassVar[dict[str, str]] = {"product.template": "product_tmpl_id"}

            product_tmpl_id = Many2one(
                "product.template", required=True, agent_hint="Plantilla", examples=["1"]
            )
            barcode = Char(agent_hint="Código de barras", examples=["7801234567890"])

        registry = Registry.build([Module("product", models=[Template, Product])])
        model = registry["product.product"]
        assert "name" in model.fields  # delegado
        assert model.fields["name"].delegated_from == "product.template"
        assert model.fields["barcode"].delegated_from is None

    def test_inherits_requires_existing_link_field(self) -> None:
        class Broken(Model):
            _name = "broken.product"
            _description = "Roto"
            _inherits: ClassVar[dict[str, str]] = {"product.template": "falta_id"}

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("broken", models=[Broken])])
        assert exc.value.code in ("MODEL_NOT_FOUND", "INHERITS_LINK_FIELD_MISSING")


class TestModuleGraph:
    def test_topological_order(self) -> None:
        class Ext1(Model):
            _inherit = "sale.order"

            a = Char(agent_hint="A", examples=["a"])

        class Ext2(Model):
            _inherit = "sale.order"

            b = Char(agent_hint="B", examples=["b"])

        registry = Registry.build(
            [
                Module("segundo", models=[Ext2], depends=["primero"]),
                Module("primero", models=[Ext1], depends=["base"]),
                base_module(),
            ]
        )
        assert {"a", "b"} <= set(registry["sale.order"].fields)

    def test_dependency_cycle_detected(self) -> None:
        with pytest.raises(KernelError) as exc:
            Registry.build(
                [
                    Module("a", models=[], depends=["b"]),
                    Module("b", models=[], depends=["a"]),
                ]
            )
        assert exc.value.code == "REGISTRY_DEPENDENCY_CYCLE"

    def test_missing_dependency_detected(self) -> None:
        with pytest.raises(KernelError) as exc:
            Registry.build([Module("solo", models=[], depends=["fantasma"])])
        assert exc.value.code == "REGISTRY_MISSING_DEPENDENCY"
