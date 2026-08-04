"""Productos: lo que se vende, se compra y —si es almacenable— se cuenta.

El costo promedio (`cost`) lo mantiene el sistema al validar recepciones;
escribirlo a mano desalinea inventario y contabilidad, por eso el hint lo
dice sin rodeos.
"""

from ordo_core.fields import Boolean, Char, Many2one, Monetary, Selection, Text
from ordo_core.model import Model

PRODUCT_TYPES = [
    ("consu", "Almacenable"),
    ("service", "Servicio"),
]

TRACKING = [
    ("none", "Sin seguimiento"),
    ("lot", "Por lote"),
    ("serial", "Por número de serie"),
]


class Product(Model):
    _name = "product.product"
    _description = "Producto o servicio"

    name = Char(
        required=True,
        index=True,
        agent_hint="Nombre comercial del producto",
        examples=["Notebook 14 pulgadas", "Hora de consultoría"],
    )
    default_code = Char(
        index=True,
        agent_hint="Referencia interna o SKU",
        examples=["NB-14-PRO"],
    )
    product_type = Selection(
        PRODUCT_TYPES,
        default="consu",
        agent_hint=(
            "Almacenable mueve stock y se valoriza; un servicio no toca el inventario nunca"
        ),
        examples=["consu", "service"],
    )
    uom_id = Many2one(
        "uom.uom",
        agent_hint="Unidad de medida en que se cuenta el producto",
        examples=["1"],
    )
    list_price = Monetary(
        agent_hint="Precio de venta de lista, sin impuestos",
        examples=["599990.00"],
    )
    cost = Monetary(
        agent_hint=(
            "Costo promedio vigente. Lo actualiza el sistema al validar "
            "recepciones; no lo escribas a mano o el inventario contable y el "
            "físico dejarán de cuadrar"
        ),
        examples=["425000.00"],
    )
    tracking = Selection(
        TRACKING,
        default="none",
        agent_hint="Si sus movimientos exigen lote o número de serie",
        examples=["none", "lot"],
    )
    income_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de ingreso propia; si falta se usa la del diario de venta",
        examples=["4"],
    )
    expense_account_id = Many2one(
        "account.account",
        agent_hint="Cuenta de gasto propia para compras; si falta, la del diario",
        examples=["5"],
    )
    barcode = Char(
        index=True,
        agent_hint="Código de barras, si existe",
        examples=["7801234567890"],
    )
    active = Boolean(default=True, agent_hint="Producto disponible", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía del producto", examples=["1"]
    )
    description = Text(
        agent_hint="Descripción larga para documentos",
        examples=["Notebook 14'' 16GB RAM"],
    )
